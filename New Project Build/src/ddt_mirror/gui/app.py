"""DDT Mirror GUI: open .stu -> pick types -> pick members/access -> generate.

Run with:  python -m ddt_mirror.gui.app   (or the ddt-mirror entry point)
"""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QPushButton, QStackedWidget, QTableWidget, QTableWidgetItem, QTabWidget,
    QTreeView, QVBoxLayout, QWidget,
)

from .. import __version__
from ..core.adopt import load_or_recover_sidecar
from ..core.engine import build_plan, type_summary
from .tree import (
    AccessDelegate, COL_ACCESS, COL_ADDRESS, build_member_model,
    collect_deselected, wire_check_propagation,
)
from .worker import BridgeWorker

# Flow: open -> pick types -> pick members/access -> choose destination ->
# (PLC mirror | SCADAPack RTU). Addresses are assigned only inside the chosen
# destination page, never before — RTU points/registers can't even be decided
# until the engineer's RemoteConnect workbook is provided.
(PAGE_OPEN, PAGE_TYPES, PAGE_MEMBERS, PAGE_DEST, PAGE_PLC,
 PAGE_RTU) = range(6)


class MainWindow(QMainWindow):
    request_open = Signal(str)
    request_apply = Signal(object, object, object, str)
    request_transfer = Signal(object, object, str, str, str)
    request_transfer_t2 = Signal(object, object, object, object,
                                 str, str, str, str)
    request_shutdown = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"DDT Mirror v{__version__} — Control Expert to HMI tag linking")
        self.resize(1000, 700)

        self.data = None          # ProjectData
        self.state = None         # SidecarState
        self.project_path = ""
        self.plan = None
        self.new_alloc = None
        self._t2_plc = None       # (plan, alloc) staged by scanner assign

        self._thread = QThread(self)
        self._worker = BridgeWorker()
        self._worker.moveToThread(self._thread)
        self.request_open.connect(self._worker.open_project)
        self.request_apply.connect(self._worker.apply)
        self.request_transfer.connect(self._worker.transfer)
        self.request_transfer_t2.connect(self._worker.transfer_t2)
        self.request_shutdown.connect(self._worker.shutdown)
        self._worker.opened.connect(self._on_opened)
        self._worker.applied.connect(self._on_applied)
        self._worker.transferred.connect(self._on_transferred)
        self._worker.t2_transferred.connect(self._on_t2_transferred)
        self._worker.failed.connect(self._on_failed)
        self._worker.progress.connect(self._on_progress)
        self._thread.start()

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.stack.addWidget(self._build_open_page())      # PAGE_OPEN
        self.stack.addWidget(self._build_types_page())     # PAGE_TYPES
        self.stack.addWidget(self._build_members_page())   # PAGE_MEMBERS
        self.stack.addWidget(self._build_dest_page())      # PAGE_DEST
        self.stack.addWidget(self._build_plc_page())       # PAGE_PLC
        self.stack.addWidget(self._build_rtu_page())       # PAGE_RTU
        self.statusBar().showMessage("Pick a Control Expert project to begin.")

    # ---------------------------------------------------------- page 0: open

    def _build_open_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addStretch()
        lay.addWidget(QLabel("<h2>1. Open Control Expert project</h2>"))
        row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText(r"C:\path\to\project.stu")
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        row.addWidget(self.path_edit)
        row.addWidget(browse)
        lay.addLayout(row)
        self.open_btn = QPushButton("Open and scan project")
        self.open_btn.clicked.connect(self._open_clicked)
        lay.addWidget(self.open_btn)
        self.open_status = QLabel("")
        self.open_status.setWordWrap(True)
        lay.addWidget(self.open_status)
        lay.addStretch()
        return page

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Control Expert project", "",
            "Control Expert projects (*.stu *.sta);;All files (*.*)")
        if path:
            self.path_edit.setText(path)

    def _open_clicked(self) -> None:
        path = self.path_edit.text().strip()
        if not os.path.isfile(path):
            QMessageBox.warning(self, "DDT Mirror", "Select an existing .stu file.")
            return
        self.open_btn.setEnabled(False)
        self.open_status.setText("Opening project (Control Expert automation "
                                 "starts on first use — this can take a minute)...")
        self.request_open.emit(path)

    def _on_opened(self, data, path: str) -> None:
        from ..core.ddt_library import apply_library, load_library

        self.data = data
        self.project_path = path
        self.state, recovery_report = load_or_recover_sidecar(path, data)
        applied = apply_library(load_library(), data, self.state)
        self.open_btn.setEnabled(True)
        self.open_status.setText("")
        self._populate_types()
        self.stack.setCurrentIndex(PAGE_TYPES)
        msg = f"{os.path.basename(path)} — {len(data.leaves)} mirrorable tags found."
        if applied:
            shown = ", ".join(applied[:6]) + (" ..." if len(applied) > 6 else "")
            msg += f"  Applied saved R/W defaults for: {shown}"
        self.statusBar().showMessage(msg)
        if recovery_report:
            QMessageBox.information(
                self, "Address map recovered", "\n".join(recovery_report))

    # --------------------------------------------------------- page 1: types

    def _build_types_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(QLabel("<h2>2. Choose tag types to share with the HMI</h2>"))
        self.types_table = QTableWidget(0, 3)
        self.types_table.setHorizontalHeaderLabels(["Type", "Kind", "Tag count"])
        self.types_table.horizontalHeader().setStretchLastSection(True)
        self.types_table.verticalHeader().setVisible(False)
        lay.addWidget(self.types_table)
        nav = QHBoxLayout()
        back = QPushButton("< Back")
        back.clicked.connect(lambda: self.stack.setCurrentIndex(PAGE_OPEN))
        nxt = QPushButton("Next >")
        nxt.clicked.connect(self._types_next)
        nav.addWidget(back)
        nav.addStretch()
        nav.addWidget(nxt)
        lay.addLayout(nav)
        return page

    def _populate_types(self) -> None:
        rows = type_summary(self.data)
        previously = set(self.state.selected_types)
        self.types_table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            name_item = QTableWidgetItem(row["type"])
            name_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            checked = (not previously) or row["type"] in previously
            name_item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            self.types_table.setItem(i, 0, name_item)
            self.types_table.setItem(i, 1, QTableWidgetItem(row["kind"]))
            self.types_table.setItem(i, 2, QTableWidgetItem(str(row["count"])))
        self.types_table.resizeColumnsToContents()

    def _types_next(self) -> None:
        chosen = [
            self.types_table.item(r, 0).text()
            for r in range(self.types_table.rowCount())
            if self.types_table.item(r, 0).checkState() == Qt.Checked
        ]
        if not chosen:
            QMessageBox.warning(self, "DDT Mirror", "Check at least one type.")
            return
        self.state.selected_types = chosen
        self._populate_members()
        self.stack.setCurrentIndex(PAGE_MEMBERS)

    # ------------------------------------------------------- page 2: members

    def _build_members_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(QLabel(
            "<h2>3. Choose members and HMI access</h2>"
            "<p>Everything is preselected — uncheck what the HMI does not need. "
            "<b>Read</b> = HMI displays the value; <b>Read/Write</b> = HMI can "
            "write it (commands, setpoints). Double-click the Access cell to "
            "change it.</p>"))
        self.type_mode_cb = QCheckBox(
            "Configure at DDT-type level: member add/remove and access changes "
            "apply to ALL variables of the same DDT type (uncheck for manual "
            "per-variable selection)")
        self.type_mode_cb.setChecked(True)
        self.type_mode_cb.toggled.connect(self._type_mode_toggled)
        lay.addWidget(self.type_mode_cb)
        self.tree = QTreeView()
        self.tree.setAlternatingRowColors(True)
        lay.addWidget(self.tree)
        nav = QHBoxLayout()
        back = QPushButton("< Back")
        back.clicked.connect(lambda: self.stack.setCurrentIndex(PAGE_TYPES))
        self.save_defaults_btn = QPushButton("Save R/W defaults for these DDTs")
        self.save_defaults_btn.setToolTip(
            "Store the current member selection and Read / Read-Write choice "
            "for every DDT type shown here, keyed by DDT type name, in a "
            "global library. Any project opened later that uses the same DDT "
            "type is configured from it automatically.")
        self.save_defaults_btn.clicked.connect(self._save_ddt_defaults)
        nxt = QPushButton("Next >")
        nxt.clicked.connect(self._members_next)
        nav.addWidget(back)
        nav.addStretch()
        nav.addWidget(self.save_defaults_btn)
        nav.addWidget(nxt)
        lay.addLayout(nav)
        return page

    def _save_ddt_defaults(self) -> None:
        from ..core.ddt_library import (
            capture_type, load_library, project_ddt_types, save_library,
        )
        from .tree import collect_deselected

        # commit the tree's current selection into the sidecar first
        per_var, type_members = collect_deselected(self.tree.model())
        self.state.deselected_leaves = per_var
        self.state.deselected_type_members = type_members

        chosen = set(self.state.selected_types)
        ddt_types = [t for t in project_ddt_types(self.data) if t in chosen]
        if not ddt_types:
            QMessageBox.information(
                self, "Save R/W defaults",
                "No DDT types are selected — only DDT types can be saved as "
                "global defaults (elementary tags stay per-project).")
            return
        answer = QMessageBox.question(
            self, "Save R/W defaults",
            "Save the current member selection and Read / Read-Write choice "
            f"as the global default for these {len(ddt_types)} DDT "
            f"type(s)?\n\n{', '.join(ddt_types)}\n\n"
            "Projects opened later that use any of these DDT types will be "
            "configured from the saved defaults automatically.")
        if answer != QMessageBox.Yes:
            return
        lib = load_library()
        for t in ddt_types:
            capture_type(lib, t, self.data, self.state)
        path = save_library(lib)
        QMessageBox.information(
            self, "Save R/W defaults",
            f"Saved defaults for {len(ddt_types)} DDT type(s) to:\n{path}")

    def _type_mode_toggled(self, checked: bool) -> None:
        if self.state is not None:
            self.state.settings.type_level_edit = checked

    def _populate_members(self) -> None:
        chosen = set(self.state.selected_types)
        leaves = [l for l in self.data.leaves
                  if (l.ddt_type or l.type_name) in chosen]
        self.type_mode_cb.setChecked(self.state.settings.type_level_edit)
        type_mode = self.type_mode_cb.isChecked
        model = build_member_model(
            leaves,
            deselected=set(self.state.deselected_leaves),
            overrides=self.state.access_overrides,
            alloc_leaves=self.state.alloc.leaves,
            type_deselected=set(self.state.deselected_type_members),
        )
        wire_check_propagation(model, type_mode)
        self.tree.setModel(model)
        self.tree.setItemDelegateForColumn(
            COL_ACCESS,
            AccessDelegate(self.state.access_overrides, type_mode, self.tree))
        # selection is address-free: addresses are assigned later, per
        # destination (PLC %M/%MW or RTU points/registers)
        self.tree.setColumnHidden(COL_ADDRESS, True)
        self.tree.expandAll()
        for col in (0, 1, 2, 4):
            self.tree.resizeColumnToContents(col)

    def _members_next(self) -> None:
        per_var, type_members = collect_deselected(self.tree.model())
        self.state.deselected_leaves = per_var
        self.state.deselected_type_members = type_members
        self.stack.setCurrentIndex(PAGE_DEST)

    # --------------------------------------------------- page 3: destination

    def _build_dest_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addStretch()
        lay.addWidget(QLabel(
            "<h2>4. Where should these tags go?</h2>"
            "<p>Your selection is the same either way — pick a destination and "
            "the app assigns the right addressing for it.</p>"))

        plc_btn = QPushButton(
            "PLC-internal mirror  →  Vijeo / Modbus HMI")
        plc_btn.setStyleSheet("text-align:left; padding:14px; font-weight:bold;")
        plc_btn.clicked.connect(self._go_plc)
        lay.addWidget(plc_btn)
        lay.addWidget(QLabel(
            "<p style='margin-left:8px;color:gray'>Creates located "
            "<code>%M</code>/<code>%MW</code> mirror variables and an ST "
            "mirror section inside this Control Expert project, then builds "
            "it. Export the Vijeo files from there.</p>"))

        rtu_btn = QPushButton(
            "SCADAPack RTU  →  RemoteConnect")
        rtu_btn.setStyleSheet("text-align:left; padding:14px; font-weight:bold;")
        rtu_btn.clicked.connect(self._go_rtu)
        lay.addWidget(rtu_btn)
        lay.addWidget(QLabel(
            "<p style='margin-left:8px;color:gray'>Assigns DNP3 points and "
            "RTU Modbus registers <b>against your RemoteConnect export</b> "
            "(so you provide it first), then produces the objects "
            "<code>.xls</code> and the Logic-Editor mirror.</p>"))

        lay.addStretch()
        nav = QHBoxLayout()
        back = QPushButton("< Back")
        back.clicked.connect(lambda: self.stack.setCurrentIndex(PAGE_MEMBERS))
        nav.addWidget(back)
        nav.addStretch()
        lay.addLayout(nav)
        return page

    def _go_plc(self) -> None:
        self._make_preview()          # %M/%MW assigned here, not before
        self.stack.setCurrentIndex(PAGE_PLC)

    def _go_rtu(self) -> None:
        self.rtu_preview.setPlainText(
            "Pick your RemoteConnect export above, then "
            "\"Assign addresses & preview\".")
        self.rtu_generate_btn.setEnabled(False)
        self._t2_plc = None
        self.stack.setCurrentIndex(PAGE_RTU)

    # ---------------------------------------------------- page 4: PLC mirror

    def _build_plc_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(QLabel("<h2>PLC-internal mirror — review and generate</h2>"))
        self.tabs = QTabWidget()
        self.preview_st = QPlainTextEdit(readOnly=True)
        self.preview_vars = QPlainTextEdit(readOnly=True)
        self.preview_csv = QPlainTextEdit(readOnly=True)
        self.preview_warn = QPlainTextEdit(readOnly=True)
        for w in (self.preview_st, self.preview_vars, self.preview_csv,
                  self.preview_warn):
            w.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.tabs.addTab(self.preview_st, "ST mirror section")
        self.tabs.addTab(self.preview_vars, "New variables")
        self.tabs.addTab(self.preview_csv, "Address map (CSV)")
        self.tabs.addTab(self.preview_warn, "Warnings")
        lay.addWidget(self.tabs)
        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        self.result_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.result_label)
        nav = QHBoxLayout()
        back = QPushButton("< Back")
        back.clicked.connect(lambda: self.stack.setCurrentIndex(PAGE_DEST))
        self.vijeo_btn = QPushButton("Export Vijeo variables...")
        self.vijeo_btn.clicked.connect(self._export_vijeo)
        self.generate_btn = QPushButton("Generate into project")
        self.generate_btn.setStyleSheet("font-weight: bold;")
        self.generate_btn.clicked.connect(self._generate_clicked)
        nav.addWidget(back)
        nav.addStretch()
        nav.addWidget(self.vijeo_btn)
        nav.addWidget(self.generate_btn)
        lay.addLayout(nav)
        return page

    def _export_vijeo(self) -> None:
        from ..codegen.vijeo import generate_vijeo_files

        scan_group, ok = QInputDialog.getText(
            self, "Vijeo export",
            "Scan group name of your Modbus TCP equipment in Vijeo\n"
            "(IO Manager > ModbusTCPIP > equipment; IEC61131 syntax must be\n"
            "enabled in its configuration):", text="ModbusEquipment01")
        if not ok or not scan_group.strip():
            return

        folder = QFileDialog.getExistingDirectory(
            self, "Folder for the two Vijeo import files",
            os.path.dirname(self.project_path) if self.project_path else "")
        if not folder:
            return
        udt_text, csv_text, warnings = generate_vijeo_files(
            self.data.types, self.data.tags, self.plan, self.state,
            scan_group.strip())
        stem = (os.path.splitext(os.path.basename(self.project_path))[0]
                if self.project_path else "ddt_mirror")
        udt_path = os.path.join(folder, f"{stem}_UDT.VJDDataTypes")
        csv_path = os.path.join(folder, f"{stem}_HMI_variables.CSV")
        with open(udt_path, "w", encoding="utf-8") as fh:
            fh.write(udt_text)
        with open(csv_path, "w", encoding="utf-8") as fh:
            fh.write(csv_text)
        msg = ("Wrote:\n"
               f"  1. {udt_path}\n"
               f"  2. {csv_path}\n\n"
               "In Vijeo Designer:\n"
               "  1. Import the UDT file FIRST (User Data Types node).\n"
               "  2. Then Variables node > Import Variables > CSV.\n"
               "Equipment: enable IEC61131 syntax, Double Word word order = "
               "'Low word first'.\n"
               "Each UDT folder has a <DDT>_Popup STRING - write an instance "
               "path into it (e.g. 'Pump1') to drive that folder's reference "
               "variables on a generic popup screen.\n\n"
               "IMPORTANT if a previous import failed halfway: Vijeo keeps "
               "everything imported before the halt and never overwrites "
               "same-name UDTs - delete the old UDTs/variables (or use a "
               "fresh project) before re-importing, or rows will be "
               "silently skipped and elements will error.")
        if warnings:
            msg += "\n\nWarnings:\n" + "\n".join(warnings[:15])
            if len(warnings) > 15:
                msg += f"\n... and {len(warnings) - 15} more"
        QMessageBox.information(self, "Vijeo export", msg)

    def _make_preview(self) -> None:
        self.result_label.setText("")
        self.plan, self.new_alloc = build_plan(
            self.data, self.state,
            project_name=os.path.basename(self.project_path))
        self.preview_st.setPlainText(self.plan.st_source)
        self.preview_vars.setPlainText("\n".join(
            f"{v['name']}  :  {v['type_name']}  AT  {v['address']}"
            for v in self.plan.new_variables) or "(no new variables needed)")
        self.preview_csv.setPlainText(self.plan.csv_text)
        self._set_warnings(self.plan.warnings)

    def _set_warnings(self, warnings: list[str]) -> None:
        self.preview_warn.setPlainText("\n".join(warnings) or "(none)")
        self.tabs.setTabText(3, f"Warnings ({len(warnings)})"
                             if warnings else "Warnings")

    # -------------------------------------------------- page 5: SCADAPack RTU

    _INDEX_OPTIONS = ["0-based (Modbus standard: register 40001 = %MW0)",
                      "1-based (register 40001 = %MW1)"]

    def _build_rtu_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(QLabel(
            "<h2>SCADAPack RTU — assign addresses and generate</h2>"
            "<p>Point this at the RTU configuration you exported from "
            "RemoteConnect. DNP3 points and Modbus registers are assigned "
            "<b>above everything already in that workbook</b>, so they can "
            "only be decided once it is provided.</p>"))

        row = QHBoxLayout()
        row.addWidget(QLabel("RemoteConnect export:"))
        self.rtu_src_edit = QLineEdit()
        self.rtu_src_edit.setPlaceholderText(
            r"C:\path\to\RTU_export.xls (Device > Export in RemoteConnect)")
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._rtu_browse)
        row.addWidget(self.rtu_src_edit)
        row.addWidget(browse)
        lay.addLayout(row)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Who runs the program:"))
        self.rtu_mode_combo = QComboBox()
        self.rtu_mode_combo.addItems([
            "RTU runs it — objects bind to Logic Editor variables",
            "PLC runs it — RTU polls the PLC via Modbus scanner",
        ])
        self.rtu_mode_combo.currentIndexChanged.connect(self._rtu_mode_changed)
        mode_row.addWidget(self.rtu_mode_combo)
        self.rtu_device_label = QLabel("PLC device in workbook:")
        self.rtu_device_edit = QLineEdit()
        self.rtu_device_edit.setPlaceholderText(
            "Modbus/TCP device name (RemoteConnect > Modbus Server Devices)")
        mode_row.addWidget(self.rtu_device_label)
        mode_row.addWidget(self.rtu_device_edit)
        lay.addLayout(mode_row)
        self._rtu_mode_changed(0)

        opts = QHBoxLayout()
        opts.addWidget(QLabel("HMI address indexing:"))
        self.rtu_index_combo = QComboBox()
        self.rtu_index_combo.addItems(self._INDEX_OPTIONS)
        opts.addWidget(self.rtu_index_combo)
        opts.addStretch()
        lay.addLayout(opts)

        self.assign_btn = QPushButton("Assign addresses & preview")
        self.assign_btn.clicked.connect(self._rtu_assign)
        lay.addWidget(self.assign_btn)

        self.rtu_preview = QPlainTextEdit(readOnly=True)
        self.rtu_preview.setLineWrapMode(QPlainTextEdit.NoWrap)
        lay.addWidget(self.rtu_preview)

        self.rtu_result = QLabel("")
        self.rtu_result.setWordWrap(True)
        self.rtu_result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.rtu_result)

        nav = QHBoxLayout()
        back = QPushButton("< Back")
        back.clicked.connect(lambda: self.stack.setCurrentIndex(PAGE_DEST))
        self.rtu_generate_btn = QPushButton("Generate transfer bundle")
        self.rtu_generate_btn.setStyleSheet("font-weight: bold;")
        self.rtu_generate_btn.setEnabled(False)
        self.rtu_generate_btn.clicked.connect(self._rtu_generate)
        nav.addWidget(back)
        nav.addStretch()
        nav.addWidget(self.rtu_generate_btn)
        lay.addLayout(nav)
        return page

    def _rtu_mode_changed(self, index: int) -> None:
        scanner = index == 1
        self.rtu_device_label.setVisible(scanner)
        self.rtu_device_edit.setVisible(scanner)
        self._t2_plc = None
        if hasattr(self, "rtu_generate_btn"):  # exists after page build
            self.rtu_generate_btn.setEnabled(False)

    def _rtu_browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Your RTU configuration exported from RemoteConnect",
            os.path.dirname(self.project_path),
            "RemoteConnect export (*.xls);;All files (*.*)")
        if path:
            self.rtu_src_edit.setText(path)

    def _rtu_assign(self) -> None:
        src = self.rtu_src_edit.text().strip()
        if not os.path.isfile(src):
            QMessageBox.warning(self, "SCADAPack RTU",
                                "Pick your RemoteConnect .xls export first.")
            return
        self.state.settings.hmi_index_base = self.rtu_index_combo.currentIndex()
        self.statusBar().showMessage("Assigning addresses against the "
                                     "workbook...")
        QApplication.processEvents()
        try:
            if self.rtu_mode_combo.currentIndex() == 1:
                self._rtu_assign_scanner(src)
            else:
                self._rtu_assign_logic(src)
        except Exception as exc:  # bad/locked workbook, wrong file, ...
            self.statusBar().showMessage("Assignment failed.")
            self.rtu_generate_btn.setEnabled(False)
            QMessageBox.critical(self, "SCADAPack RTU", str(exc))

    def _rtu_assign_logic(self, src: str) -> None:
        from ..codegen.remoteconnect import preview_remoteconnect

        pv = preview_remoteconnect(self.data, self.state, src)
        header = (f"Device type: {pv.device_type or 'unknown'}\n"
                  f"{pv.created} new objects to add; {pv.existing} already in "
                  f"the workbook (untouched).\n"
                  f"Warnings: {len(pv.warnings)}\n"
                  + "=" * 60 + "\n")
        warn = ("\n\n--- warnings ---\n" + "\n".join(pv.warnings)
                if pv.warnings else "")
        self.rtu_preview.setPlainText(header + pv.map_csv + warn)
        self.rtu_generate_btn.setEnabled(True)
        self.statusBar().showMessage(
            f"Assigned: {pv.created} new objects. Review, then generate.")

    def _rtu_assign_scanner(self, src: str) -> None:
        from ..codegen.scanner import generate_t2_map_csv, plan_scanner

        device = self.rtu_device_edit.text().strip()
        if not device:
            QMessageBox.warning(
                self, "SCADAPack RTU",
                "Enter the PLC's Modbus device name as configured in "
                "RemoteConnect (Modbus Server Devices).")
            return
        plc_plan, plc_alloc = build_plan(
            self.data, self.state,
            project_name=os.path.basename(self.project_path),
            word_bools=True)
        t2 = plan_scanner(self.data, self.state, plc_plan.assignments,
                          src, device)
        self._t2_plc = (plc_plan, plc_alloc)
        n_copies = sum(1 for a in plc_plan.assignments if not a.premapped)
        header = (
            f"PLC side: {len(plc_plan.new_variables)} mirror variables + "
            f"{n_copies} ST copies (BOOLs as %MW words) - generated into "
            "the Control Expert project on Generate.\n"
            f"RTU side: device '{t2.device.name}', "
            f"{t2.created_objects} new objects, "
            f"{len(t2.new_blocks)} new scan blocks, "
            f"{len(t2.bind_rows)} register bindings.\n"
            f"Warnings: {len(t2.warnings)}\n" + "=" * 60 + "\n")
        warn = ("\n\n--- warnings ---\n" + "\n".join(t2.warnings)
                if t2.warnings else "")
        self.rtu_preview.setPlainText(
            header + generate_t2_map_csv(t2.map_rows) + warn)
        self.rtu_generate_btn.setEnabled(True)
        self.statusBar().showMessage(
            f"Assigned: {t2.created_objects} objects over "
            f"{len(t2.new_blocks)} scan blocks. Review, then generate.")

    def _rtu_generate(self) -> None:
        src = self.rtu_src_edit.text().strip()
        if not os.path.isfile(src):
            QMessageBox.warning(self, "SCADAPack RTU",
                                "Pick your RemoteConnect .xls export first.")
            return
        scanner_mode = self.rtu_mode_combo.currentIndex() == 1
        if scanner_mode:
            if not self._t2_plc:
                QMessageBox.warning(self, "SCADAPack RTU",
                                    "Run 'Assign addresses & preview' first.")
                return
            plan, _alloc = self._t2_plc
            answer = QMessageBox.question(
                self, "SCADAPack RTU",
                "This will create the PLC mirror inside the Control Expert "
                f"project ({len(plan.new_variables)} variables + the "
                f"'{self.state.settings.section_name}' ST section, rebuild "
                "and save), then write the enriched RemoteConnect .xls."
                "\n\nContinue?")
            if answer != QMessageBox.Yes:
                return
        folder = QFileDialog.getExistingDirectory(
            self, "Folder for the transfer bundle",
            os.path.dirname(self.project_path))
        if not folder:
            return
        self.state.settings.hmi_index_base = self.rtu_index_combo.currentIndex()
        self.rtu_generate_btn.setEnabled(False)
        if scanner_mode:
            plan, alloc = self._t2_plc
            self.request_transfer_t2.emit(
                self.data, plan, alloc, self.state, src, folder,
                self.project_path, self.rtu_device_edit.text().strip())
        else:
            self.request_transfer.emit(self.data, self.state, src, folder,
                                       self.project_path)

    def _on_t2_transferred(self, apply_report, t2_report) -> None:
        self.rtu_generate_btn.setEnabled(True)
        if not apply_report.ok:
            self.statusBar().showMessage("PLC mirror failed - nothing "
                                         "written to the workbook.")
            QMessageBox.critical(
                self, "SCADAPack RTU",
                f"The PLC mirror step failed:\n{apply_report.error}\n\n"
                "The RemoteConnect workbook was NOT modified.")
            return
        self.statusBar().showMessage("T2 bundle complete.")
        msg = ("PLC project updated: "
               f"{len(apply_report.created_vars)} mirror variables created "
               f"(skipped {len(apply_report.skipped_vars)} existing), "
               f"build {apply_report.build_state}, saved.\n\nWrote:\n"
               f"  1. {t2_report.xls_path}\n"
               f"     ({t2_report.created_objects} new objects, "
               f"{t2_report.new_blocks} scan blocks, "
               f"{t2_report.new_bindings} register bindings on device "
               f"'{t2_report.device}')\n"
               f"  2. {t2_report.map_path}\n\n"
               "In RemoteConnect:\n"
               "  1. Import the .xls (full configuration plus the new "
               "objects and scanner blocks).\n"
               "  2. Write the RTU configuration to the device - the "
               "scanner starts polling the PLC; no Logic Editor program "
               "is needed for these objects.\n"
               "  3. Point the HMI at the RtuRegister/HmiAddress column "
               "and GeoSCADA at the DNP3 points in the map CSV.")
        warnings = list(apply_report.warnings) + list(t2_report.warnings)
        if warnings:
            msg += "\n\nWarnings:\n" + "\n".join(warnings[:12])
            if len(warnings) > 12:
                msg += f"\n... and {len(warnings) - 12} more"
        QMessageBox.information(self, "Transfer to RemoteConnect (T2)", msg)

    def _on_transferred(self, report) -> None:
        self.rtu_generate_btn.setEnabled(True)
        self.statusBar().showMessage("Transfer bundle complete.")
        rc = report.rc
        msg = ("Wrote:\n"
               f"  1. {rc.xls_path}\n"
               f"     ({rc.created} new objects; {rc.existing} already in "
               "the workbook, untouched)\n"
               f"  2. {rc.st_path}\n"
               f"  3. {rc.map_path}\n"
               f"  4. {report.xsy_path}\n"
               f"     ({report.xsy_removed} generated mirror variables "
               "removed)\n"
               f"  5. {report.sections_dir}\\ "
               f"({len(report.section_files)} section files)\n")
        msg += ("\nIn RemoteConnect:\n"
                "  1. Import the .xls (your full configuration plus the "
                "new objects).\n"
                "  2. Logic Editor: import the .xsy variables file, then "
                "import the section files one by one (in their numbered "
                "order).\n"
                "  3. Create a new ST section (MAST, last in order) and "
                "paste the mirror .st file.\n"
                "  4. Build — object variables bind to the names "
                "automatically (values are the .value member).")
        if report.warnings:
            msg += "\n\nWarnings:\n" + "\n".join(report.warnings[:12])
            if len(report.warnings) > 12:
                msg += f"\n... and {len(report.warnings) - 12} more"
        QMessageBox.information(self, "Transfer to RemoteConnect", msg)

    def _generate_clicked(self) -> None:
        n_vars = len(self.plan.new_variables)
        n_copies = sum(1 for a in self.plan.assignments if not a.premapped)
        answer = QMessageBox.question(
            self, "DDT Mirror",
            f"This will create {n_vars} mirror variables, write the "
            f"'{self.state.settings.section_name}' ST section ({n_copies} "
            "copies), rebuild and save the project.\n\nContinue?")
        if answer != QMessageBox.Yes:
            return
        self.generate_btn.setEnabled(False)
        self.request_apply.emit(self.plan, self.new_alloc, self.state,
                                self.project_path)

    def _on_applied(self, report) -> None:
        self.generate_btn.setEnabled(True)
        if report.ok:
            text = (
                f"<b style='color:green'>Done.</b> Created "
                f"{len(report.created_vars)} variables "
                f"(skipped {len(report.skipped_vars)} existing), build "
                f"{report.build_state}, project saved.<br>"
                f"Address map: {report.csv_path or '(not written)'}<br>"
                f"Sidecar: {report.sidecar_path}")
            for w in report.warnings:
                text += f"<br><b style='color:darkorange'>Warning:</b> {w}"
            self.result_label.setText(text)
            self.statusBar().showMessage("Generation complete.")
        else:
            self.result_label.setText(
                f"<b style='color:red'>Failed:</b> {report.error}")
            if report.build_output:
                self.preview_warn.setPlainText(report.build_output)
                self.tabs.setCurrentWidget(self.preview_warn)

    # ------------------------------------------------------------- plumbing

    def _on_progress(self, msg: str) -> None:
        self.statusBar().showMessage(msg)

    def _on_failed(self, tb: str) -> None:
        self.open_btn.setEnabled(True)
        self.generate_btn.setEnabled(True)
        self.rtu_generate_btn.setEnabled(True)
        self.open_status.setText("")
        QMessageBox.critical(self, "DDT Mirror — error", tb[-2000:])
        self.statusBar().showMessage("Error — see dialog.")

    def closeEvent(self, event) -> None:
        self.request_shutdown.emit()
        self._thread.quit()
        self._thread.wait(15_000)
        super().closeEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
