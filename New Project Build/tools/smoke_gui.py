"""Headless GUI smoke test: drives the wizard with fixture data, no COM.

Verifies page flow, tree building, check propagation, access editing via the
delegate's data path, and preview generation.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# isolate the global DDT library to a scratch file (never touch real config)
import tempfile as _tf

os.environ["DDT_MIRROR_LIBRARY"] = os.path.join(
    _tf.mkdtemp(prefix="ddtlib_smoke_"), "ddt_library.json")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from ddt_mirror.core.engine import ProjectData
from ddt_mirror.core.flatten import flatten_tags
from ddt_mirror.core.persist import SidecarState
from ddt_mirror.core.xsy_parser import parse_xsy
from ddt_mirror.gui.app import (
    MainWindow, PAGE_DEST, PAGE_MEMBERS, PAGE_PLC, PAGE_RTU, PAGE_TYPES,
)
from ddt_mirror.gui.tree import COL_ACCESS, COL_MEMBER, LEAF_ROLE

FIXTURE = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures",
                       "sample.xsy")


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"  OK: {msg}")


def main() -> None:
    app = QApplication(sys.argv)
    win = MainWindow()

    with open(FIXTURE, encoding="utf-8") as fh:
        types, tags = parse_xsy(fh.read())
    leaves, warnings = flatten_tags(tags, types)
    data = ProjectData(types=types, tags=tags, leaves=leaves, warnings=warnings)

    # simulate a successful open (bypasses COM)
    win.state = SidecarState()
    win.data = data
    win.project_path = os.path.abspath("fixture_project.stu")
    win._populate_types()
    win.stack.setCurrentIndex(PAGE_TYPES)
    check(win.types_table.rowCount() >= 4, "types page lists DDT + elementary types")
    check(all(win.types_table.item(r, 0).checkState() == Qt.Checked
              for r in range(win.types_table.rowCount())),
          "all types preselected on first run")

    win._types_next()
    check(win.stack.currentIndex() == PAGE_MEMBERS, "advanced to members page")
    model = win.tree.model()

    # find Pump1 group and its Cmd child
    pump1 = next(model.item(r, COL_MEMBER) for r in range(model.rowCount())
                 if model.item(r, COL_MEMBER).text().startswith("Pump1"))
    check(pump1.hasChildren(), "Pump1 renders as a group with member children")
    check(pump1.checkState() == Qt.Checked, "members preselected")

    cmd_row = next(r for r in range(pump1.rowCount())
                   if pump1.child(r, COL_MEMBER).text() == "Cmd")
    check(pump1.child(cmd_row, COL_ACCESS).text() == "Read/Write",
          "Cmd preset to Read/Write by naming convention")

    pump2 = next(model.item(r, COL_MEMBER) for r in range(model.rowCount())
                 if model.item(r, COL_MEMBER).text().startswith("Pump2"))

    def row_of(group, member):
        return next(r for r in range(group.rowCount())
                    if group.child(r, COL_MEMBER).text() == member)

    # TYPE MODE (default): unchecking a member applies to all instances
    check(win.type_mode_cb.isChecked(), "type-level mode is the default")
    pump1.child(row_of(pump1, "Ctrl.Mode"), COL_MEMBER).setCheckState(Qt.Unchecked)
    check(pump1.checkState() == Qt.PartiallyChecked,
          "parent shows tri-state after unchecking a member")
    check(pump2.child(row_of(pump2, "Ctrl.Mode"), COL_MEMBER).checkState()
          == Qt.Unchecked,
          "type mode: uncheck propagates to every instance of the DDT type")

    # MANUAL MODE: unchecking affects only the one variable
    win.type_mode_cb.setChecked(False)
    pump1.child(row_of(pump1, "Flow_PV"), COL_MEMBER).setCheckState(Qt.Unchecked)
    check(pump2.child(row_of(pump2, "Flow_PV"), COL_MEMBER).checkState()
          == Qt.Checked,
          "manual mode: uncheck stays on the single variable")
    win.type_mode_cb.setChecked(True)

    # simulate an access edit through the delegate's model-data path
    delegate = win.tree.itemDelegateForColumn(COL_ACCESS)
    running_leaf = pump1.child(row_of(pump1, "Running"), COL_MEMBER).data(LEAF_ROLE)
    win.state.access_overrides[running_leaf.access_key] = "read_write"
    delegate._apply_to_matches(model, running_leaf.access_key, "Read/Write")
    check(pump2.child(row_of(pump2, "Running"), COL_ACCESS).text() == "Read/Write",
          "access edit propagates to every instance of the DDT type")

    # members page hides the address column — selection is address-free
    from ddt_mirror.gui.tree import COL_ADDRESS

    check(win.tree.isColumnHidden(COL_ADDRESS),
          "address column hidden during selection (addresses assigned later)")

    win._members_next()
    check(win.stack.currentIndex() == PAGE_DEST,
          "members Next goes to the destination choice, not straight to output")
    check(win.state.deselected_type_members == ["PUMP_T|Ctrl.Mode"],
          "member unchecked on all instances stored as a TYPE-level exclusion")

    # no addresses assigned yet — that happens only inside a destination
    check(win.plan is None, "no address plan built before a destination is chosen")

    # choosing the PLC destination assigns %M/%MW and builds the preview
    win._go_plc()
    check(win.stack.currentIndex() == PAGE_PLC, "PLC destination opens the PLC page")
    check(win.plan is not None, "PLC destination assigns addresses (plan built)")

    # ---- global DDT R/W library: save current config, then re-apply to a
    # fresh sidecar and confirm the type-level access + deselection carry over
    from ddt_mirror.core.ddt_library import (
        apply_library, capture_type, load_library, save_library,
    )

    lib = load_library()
    capture_type(lib, "PUMP_T", data, win.state)
    save_library(lib)
    check("PUMP_T" in load_library().types, "DDT R/W defaults saved to library")
    fresh = SidecarState()
    applied = apply_library(load_library(), data, fresh)
    check(applied == ["PUMP_T"], "library applies to a matching DDT type")
    check(fresh.access_overrides.get("PUMP_T|Running") == "read_write",
          "saved access re-applied to a new project's sidecar")
    check("PUMP_T|Ctrl.Mode" in fresh.deselected_type_members,
          "saved member deselection re-applied to a new project")
    check(win.state.deselected_leaves == ["Pump1.Flow_PV"],
          "partially unchecked member stays a per-variable exclusion")
    st = win.preview_st.toPlainText()
    check("Pump1.Cmd := %M" in st, "preview ST: R/W BOOL copied from coil")
    check("HMI_Pump2_Flow_PV" in st and "HMI_Pump1_Flow_PV" not in st,
          "preview ST: REAL mirror var only for the still-selected instance")
    check("Ctrl.Mode" not in st, "type-deselected member absent from ST")
    check("Pump1.Running := %M" in st,
          "flipped access honored in ST (Running now HMI-written)")
    check(win.plan.csv_text.count("\n") > 8, "CSV preview populated")
    page = win.stack.widget(PAGE_PLC)
    check(win.generate_btn.isVisibleTo(page) and win.vijeo_btn.isVisibleTo(page),
          "PLC page shows Generate + Vijeo (RTU lives on its own page)")

    # ---- SCADAPack RTU page: assign against a workbook, review, then the
    # generate button unlocks. Addresses are decided here, not earlier.
    check(win.state.settings.hmi_index_base == 0, "HMI index base defaults to 0")
    win.stack.setCurrentIndex(PAGE_DEST)
    win._go_rtu()
    check(win.stack.currentIndex() == PAGE_RTU, "RTU destination opens the RTU page")
    check(not win.rtu_generate_btn.isEnabled(),
          "RTU generate disabled until addresses are assigned")

    from ddt_mirror.codegen.remoteconnect import preview_remoteconnect

    rc_xls = os.path.join(os.path.dirname(__file__), "..", "remoteconnect.xls")
    if os.path.isfile(rc_xls):
        win.rtu_src_edit.setText(rc_xls)
        win._rtu_assign()
        check(win.rtu_generate_btn.isEnabled(),
              "RTU generate unlocks after a successful assign")
        check("HmiAddress" in win.rtu_preview.toPlainText(),
              "RTU preview shows the point/register/HMI-address map")
        pv = preview_remoteconnect(win.data, win.state, rc_xls)
        check("Pump2_Flow_PV" in pv.map_csv and pv.created > 0,
              "RTU preview is pure and assigns new objects")
    else:
        print("  (skip: remoteconnect.xls fixture not present)")

    win.request_shutdown.emit()
    win._thread.quit()
    win._thread.wait(5000)
    print("\nGUI SMOKE TEST PASSED")


if __name__ == "__main__":
    main()
