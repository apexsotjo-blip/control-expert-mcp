"""DDT Mirror workspace: one window, everything visible.

Layout: header (open project) | left = tag selection (types checklist +
member tree, live filter) | right = destination tabs (Overview, PLC
mirror -> Vijeo, SCADAPack RTU) | bottom = activity log.

Selection stays address-free; each destination tab assigns its own
addressing on Preview/Assign, exactly like the engine has always worked.

Run with:  python -m ddt_mirror.gui.app   (or the ddt-mirror entry point)
"""

from __future__ import annotations

import datetime as _dt
import os
import sys

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QSplitter,
    QTabWidget, QTreeView, QVBoxLayout, QWidget,
)

from .. import __version__
from ..core.adopt import load_or_recover_sidecar
from ..core.engine import build_plan, type_summary
from .theme import GREEN, ORANGE, RED, apply_theme
from .tree import (
    AccessDelegate, COL_ACCESS, COL_ADDRESS, COL_MEMBER,
    build_member_model, collect_deselected, wire_check_propagation,
)
from .worker import BridgeWorker

TAB_OVERVIEW, TAB_PLC, TAB_RTU = range(3)


class MainWindow(QMainWindow):
    request_open = Signal(str)
    request_apply = Signal(object, object, object, str)
    request_transfer = Signal(object, object, str, str, str)
    request_transfer_t2 = Signal(object, object, object, object,
                                 str, str, str, str)
    request_shutdown = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(
            f"DDT Mirror v{__version__} — Control Expert to HMI/RTU/SCADA")
        self.resize(1280, 800)

        self.data = None          # ProjectData
        self.state = None         # SidecarState
        self.project_path = ""
        self.plan = None
        self.new_alloc = None
        self._t2_plc = None       # (plan, alloc) staged by scanner assign
        self._syncing_types = False

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

        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(14, 12, 14, 8)
        outer.setSpacing(10)
        outer.addLayout(self._build_header())

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self._build_selection_panel())
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_overview_tab(), "Overview")
        self.tabs.addTab(self._build_plc_tab(), "PLC mirror → Vijeo")
        self.tabs.addTab(self._build_rtu_tab(), "SCADAPack RTU")
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        outer.addWidget(split, stretch=1)

        self.activity = QPlainTextEdit(readOnly=True)
        self.activity.setMaximumHeight(140)
        self.activity.setPlaceholderText("Activity log")
        self.activity.setProperty("class", "mono")
        outer.addWidget(self.activity)

        self.setCentralWidget(central)
        self._set_project_loaded(False)
        self.statusBar().showMessage("Open a Control Expert project to begin.")

    # ------------------------------------------------------------- plumbing

    def _log(self, text: str) -> None:
        stamp = _dt.datetime.now().strftime("%H:%M:%S")
        for line in text.splitlines() or [""]:
            self.activity.appendPlainText(f"[{stamp}] {line}")
        sb = self.activity.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _set_project_loaded(self, loaded: bool) -> None:
        self.selection_panel.setEnabled(loaded)
        self.tabs.setEnabled(loaded)

    # --------------------------------------------------------------- header

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)
        title = QLabel(f"<b style='font-size:13pt'>DDT Mirror</b> "
                       f"<span style='color:#8b90a0'>v{__version__}</span>")
        row.addWidget(title)
        self.open_btn = QPushButton("Open project (.stu)...")
        self.open_btn.setProperty("kind", "primary")
        self.open_btn.clicked.connect(self._open_clicked)
        row.addWidget(self.open_btn)
        self.project_label = QLabel("<i>no project open</i>")
        self.project_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(self.project_label, stretch=1)
        return row

    def _open_clicked(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Control Expert project", "",
            "Control Expert projects (*.stu *.sta);;All files (*.*)")
        if not path:
            return
        self.open_btn.setEnabled(False)
        self.project_label.setText(
            "Opening (Control Expert automation starts on first use — "
            "this can take a minute)...")
        self._log(f"Opening {path}")
        self.request_open.emit(path)

    def _on_opened(self, data, path: str) -> None:
        from ..core.ddt_library import apply_library, load_library

        self.data = data
        self.project_path = path
        self.state, recovery_report = load_or_recover_sidecar(path, data)
        applied = apply_library(load_library(), data, self.state)
        self.open_btn.setEnabled(True)
        self.project_label.setText(
            f"<b>{os.path.basename(path)}</b> — {len(data.leaves)} "
            "mirrorable tags")
        self._populate_workspace()
        self._set_project_loaded(True)
        self._log(f"Opened {os.path.basename(path)}: {len(data.leaves)} "
                  f"mirrorable tags, {len(data.types)} DDT types.")
        if applied:
            self._log("Applied saved R/W defaults for: " + ", ".join(applied))
        if recovery_report:
            self._log("\n".join(recovery_report))
            QMessageBox.information(
                self, "Address map recovered", "\n".join(recovery_report))
        self.statusBar().showMessage("Project loaded — review the selection, "
                                     "then use a destination tab.")

    # ------------------------------------------------------ selection panel

    def _build_selection_panel(self) -> QWidget:
        self.selection_panel = QWidget()
        lay = QVBoxLayout(self.selection_panel)
        lay.addWidget(QLabel("<b>1 — Types shared with the HMI/RTU</b>"))
        self.types_list = QListWidget()
        self.types_list.setMaximumHeight(170)
        self.types_list.itemChanged.connect(self._on_type_toggled)
        lay.addWidget(self.types_list)

        members_hdr = QLabel(
            "<b>2 — Members and access</b> "
            "<span style='color:#8b90a0'>(uncheck what the HMI does not need; "
            "double-click Access to change Read / Read-Write)</span>")
        members_hdr.setWordWrap(True)
        lay.addWidget(members_hdr)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter members...")
        self.filter_edit.textChanged.connect(self._apply_filter)
        lay.addWidget(self.filter_edit)
        self.type_mode_cb = QCheckBox("Type-level editing")
        self.type_mode_cb.setToolTip(
            "Checked: member add/remove and access changes apply to ALL "
            "variables of the same DDT type.\nUnchecked: manual per-variable "
            "selection.")
        self.type_mode_cb.setChecked(True)
        self.type_mode_cb.toggled.connect(self._type_mode_toggled)
        lay.addWidget(self.type_mode_cb)
        self.tree = QTreeView()
        self.tree.setAlternatingRowColors(True)
        lay.addWidget(self.tree, stretch=1)
        self.save_defaults_btn = QPushButton("Save R/W defaults for these DDTs")
        self.save_defaults_btn.setToolTip(
            "Store member selection + Read/Read-Write per DDT type name in "
            "a global library; any later project using the same DDT type is "
            "configured automatically.")
        self.save_defaults_btn.clicked.connect(self._save_ddt_defaults)
        lay.addWidget(self.save_defaults_btn)
        return self.selection_panel

    def _populate_workspace(self) -> None:
        self._populate_types()
        self._populate_members()
        self._populate_overview()
        self.plan = None
        self._t2_plc = None
        self.result_label.setText("")
        self.rtu_result.setText("")
        self.rtu_generate_btn.setEnabled(False)

    def _populate_types(self) -> None:
        rows = type_summary(self.data)
        previously = set(self.state.selected_types)
        self._syncing_types = True
        self.types_list.clear()
        for row in rows:
            item = QListWidgetItem(
                f"{row['type']}   ({row['kind']}, {row['count']} tags)")
            item.setData(Qt.UserRole, row["type"])
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            checked = (not previously) or row["type"] in previously
            item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            self.types_list.addItem(item)
        self._syncing_types = False
        self.state.selected_types = self._checked_types()

    def _checked_types(self) -> list[str]:
        return [self.types_list.item(i).data(Qt.UserRole)
                for i in range(self.types_list.count())
                if self.types_list.item(i).checkState() == Qt.Checked]

    def _on_type_toggled(self, _item) -> None:
        if self._syncing_types:
            return
        self._commit_selection()          # keep member choices of shown types
        self.state.selected_types = self._checked_types()
        self._populate_members()
        self.plan = None                  # selection changed: previews stale
        self._t2_plc = None
        self.rtu_generate_btn.setEnabled(False)

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
        # selection is address-free: addresses are assigned per destination
        self.tree.setColumnHidden(COL_ADDRESS, True)
        self.tree.expandAll()
        for col in (0, 1, 2, 4):
            self.tree.resizeColumnToContents(col)
        self._apply_filter(self.filter_edit.text())

    def _apply_filter(self, text: str) -> None:
        model = self.tree.model()
        if model is None:
            return
        needle = text.strip().lower()
        root = model.invisibleRootItem()
        for r in range(root.rowCount()):
            group = root.child(r, COL_MEMBER)
            if not group.hasChildren():
                hide = bool(needle) and needle not in group.text().lower()
                self.tree.setRowHidden(r, root.index(), hide)
                continue
            any_child = False
            for cr in range(group.rowCount()):
                child = group.child(cr, COL_MEMBER)
                hide = bool(needle) and needle not in child.text().lower() \
                    and needle not in group.text().lower()
                self.tree.setRowHidden(cr, group.index(), hide)
                any_child = any_child or not hide
            self.tree.setRowHidden(r, root.index(),
                                   bool(needle) and not any_child)

    def _commit_selection(self) -> None:
        """Fold the tree's current check state into the sidecar, keeping
        exclusions of types that are currently hidden (unchecked)."""
        model = self.tree.model()
        if model is None or self.state is None:
            return
        shown = set(self.state.selected_types)

        def leaf_type(path: str) -> str | None:
            leaf = self._leaf_by_path.get(path)
            return (leaf.ddt_type or leaf.type_name) if leaf else None

        per_var, type_members = collect_deselected(model)
        kept_vars = [p for p in self.state.deselected_leaves
                     if leaf_type(p) not in shown]
        kept_types = [k for k in self.state.deselected_type_members
                      if k.split("|", 1)[0] not in shown]
        self.state.deselected_leaves = kept_vars + per_var
        self.state.deselected_type_members = kept_types + type_members

    @property
    def _leaf_by_path(self) -> dict:
        return {l.full_path: l for l in (self.data.leaves if self.data else [])}

    def _type_mode_toggled(self, checked: bool) -> None:
        if self.state is not None:
            self.state.settings.type_level_edit = checked

    def _save_ddt_defaults(self) -> None:
        from ..core.ddt_library import (
            capture_type, load_library, project_ddt_types, save_library,
        )

        self._commit_selection()
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
            f"type(s)?\n\n{', '.join(ddt_types)}")
        if answer != QMessageBox.Yes:
            return
        lib = load_library()
        for t in ddt_types:
            capture_type(lib, t, self.data, self.state)
        path = save_library(lib)
        self._log(f"Saved R/W defaults for {', '.join(ddt_types)} -> {path}")

    # --------------------------------------------------------- overview tab

    def _build_overview_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        self.overview_label = QLabel("")
        self.overview_label.setWordWrap(True)
        self.overview_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.overview_label)
        lay.addWidget(QLabel("<b>Project warnings</b>"))
        self.overview_warnings = QPlainTextEdit(readOnly=True)
        self.overview_warnings.setProperty("class", "mono")
        lay.addWidget(self.overview_warnings, stretch=1)
        return page

    def _populate_overview(self) -> None:
        d, s = self.data, self.state
        reserved = d.reserved
        floor = ("none found" if reserved.max_bit < 0 and reserved.max_word < 0
                 else f"%M up to {reserved.max_bit}, %MW up to "
                      f"{reserved.max_word} ({reserved.n_located} located "
                      f"variables, {reserved.n_literals} code literals)")
        adopted = len(d.generated_sections)
        self.overview_label.setText(
            f"<h3>{os.path.basename(self.project_path)}</h3>"
            f"<b>Mirrorable tags:</b> {len(d.leaves)} &nbsp; "
            f"<b>DDT types:</b> {len(d.types)}<br>"
            f"<b>Existing address usage (allocation floor):</b> {floor}<br>"
            f"<b>Generated mirror sections found:</b> {adopted}<br>"
            f"<b>Allocated so far:</b> {len(s.alloc.leaves)} PLC mirrors, "
            f"{len(s.rtu.entries)} RTU objects<br>"
            f"<b>Sidecar:</b> next to the project "
            "(&lt;project&gt;.hmimirror.json) — addresses are append-only "
            "and never reshuffled.")
        self.overview_warnings.setPlainText(
            "\n".join(d.warnings) or "(none)")

    # -------------------------------------------------------------- PLC tab

    def _build_plc_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        top = QHBoxLayout()
        self.plc_preview_btn = QPushButton("Assign %M/%MW && preview")
        self.plc_preview_btn.clicked.connect(self._plc_preview_clicked)
        top.addWidget(self.plc_preview_btn)
        top.addStretch()
        self.vijeo_btn = QPushButton("Export Vijeo variables...")
        self.vijeo_btn.clicked.connect(self._export_vijeo)
        self.vijeo_btn.setEnabled(False)
        top.addWidget(self.vijeo_btn)
        self.generate_btn = QPushButton("Generate into project")
        self.generate_btn.setProperty("kind", "primary")
        self.generate_btn.clicked.connect(self._generate_clicked)
        self.generate_btn.setEnabled(False)
        top.addWidget(self.generate_btn)
        lay.addLayout(top)

        self.plc_tabs = QTabWidget()
        self.preview_st = QPlainTextEdit(readOnly=True)
        self.preview_vars = QPlainTextEdit(readOnly=True)
        self.preview_csv = QPlainTextEdit(readOnly=True)
        self.preview_warn = QPlainTextEdit(readOnly=True)
        for w in (self.preview_st, self.preview_vars, self.preview_csv,
                  self.preview_warn):
            w.setLineWrapMode(QPlainTextEdit.NoWrap)
            w.setProperty("class", "mono")
        self.plc_tabs.addTab(self.preview_st, "ST mirror section")
        self.plc_tabs.addTab(self.preview_vars, "New variables")
        self.plc_tabs.addTab(self.preview_csv, "Address map (CSV)")
        self.plc_tabs.addTab(self.preview_warn, "Warnings")
        lay.addWidget(self.plc_tabs, stretch=1)

        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        self.result_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.result_label)
        return page

    def _plc_preview_clicked(self) -> None:
        self._commit_selection()
        self._make_preview()
        self.vijeo_btn.setEnabled(True)
        self.generate_btn.setEnabled(True)
        self.statusBar().showMessage(
            f"{len(self.plan.new_variables)} mirror variables planned. "
            "Review, then Generate.")

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
        self.plc_tabs.setTabText(3, f"Warnings ({len(warnings)})"
                                 if warnings else "Warnings")

    def _export_vijeo(self) -> None:
        from ..codegen.vijeo import generate_vijeo_files

        if self.plan is None:
            return
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
        self._log(f"Vijeo export:\n  {udt_path}\n  {csv_path}")
        for w in warnings:
            self._log(f"  warning: {w}")
        QMessageBox.information(
            self, "Vijeo export",
            f"Wrote:\n  1. {udt_path}\n  2. {csv_path}\n\n"
            "In Vijeo Designer: import the UDT file FIRST (User Data Types "
            "node), then Variables > Import Variables > CSV.\n"
            "Equipment: IEC61131 syntax ON, Double Word order 'Low word "
            "first'.\nIf a previous import failed halfway, delete the old "
            "UDTs/variables (or use a fresh project) before re-importing."
            + ("\n\nWarnings:\n" + "\n".join(warnings[:10]) if warnings
               else ""))

    def _generate_clicked(self) -> None:
        if self.plan is None:
            return
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
                f"<b style='color:{GREEN}'>Done.</b> Created "
                f"{len(report.created_vars)} variables "
                f"(skipped {len(report.skipped_vars)} existing), build "
                f"{report.build_state}, project saved.<br>"
                f"Address map: {report.csv_path or '(not written)'}<br>"
                f"Sidecar: {report.sidecar_path}")
            for w in report.warnings:
                text += f"<br><b style='color:{ORANGE}'>Warning:</b> {w}"
            self.result_label.setText(text)
            self._log(f"PLC generate OK: {len(report.created_vars)} created, "
                      f"{len(report.skipped_vars)} existing, build "
                      f"{report.build_state}. Map: {report.csv_path}")
            for w in report.warnings:
                self._log(f"  warning: {w}")
            self.statusBar().showMessage("Generation complete.")
        else:
            self.result_label.setText(
                f"<b style='color:{RED}'>Failed:</b> {report.error}")
            self._log(f"PLC generate FAILED: {report.error}")
            if report.build_output:
                self.preview_warn.setPlainText(report.build_output)
                self.plc_tabs.setCurrentWidget(self.preview_warn)

    # -------------------------------------------------------------- RTU tab

    _INDEX_OPTIONS = ["0-based (Modbus standard: register 40001 = %MW0)",
                      "1-based (register 40001 = %MW1)"]

    def _build_rtu_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        intro = QLabel(
            "<span style='color:#8b90a0'>DNP3 points and RTU Modbus registers "
            "are assigned <b>above everything already in your RemoteConnect "
            "export</b> — provide it first.</span>")
        intro.setWordWrap(True)
        lay.addWidget(intro)

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
        self.rtu_mode_combo.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.rtu_mode_combo.setMinimumContentsLength(28)
        self.rtu_mode_combo.currentIndexChanged.connect(self._rtu_mode_changed)
        mode_row.addWidget(self.rtu_mode_combo)
        self.rtu_device_label = QLabel("PLC device in workbook:")
        self.rtu_device_edit = QLineEdit()
        self.rtu_device_edit.setPlaceholderText(
            "Modbus/TCP device name (RemoteConnect > Modbus Server Devices)")
        mode_row.addWidget(self.rtu_device_label)
        mode_row.addWidget(self.rtu_device_edit)
        lay.addLayout(mode_row)

        opts = QHBoxLayout()
        opts.addWidget(QLabel("HMI address indexing:"))
        self.rtu_index_combo = QComboBox()
        self.rtu_index_combo.addItems(self._INDEX_OPTIONS)
        self.rtu_index_combo.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.rtu_index_combo.setMinimumContentsLength(20)
        opts.addWidget(self.rtu_index_combo)
        opts.addStretch()
        self.assign_btn = QPushButton("Assign addresses && preview")
        self.assign_btn.clicked.connect(self._rtu_assign)
        opts.addWidget(self.assign_btn)
        self.rtu_generate_btn = QPushButton("Generate transfer bundle")
        self.rtu_generate_btn.setProperty("kind", "primary")
        self.rtu_generate_btn.setEnabled(False)
        self.rtu_generate_btn.clicked.connect(self._rtu_generate)
        opts.addWidget(self.rtu_generate_btn)
        lay.addLayout(opts)

        self.rtu_preview = QPlainTextEdit(readOnly=True)
        self.rtu_preview.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.rtu_preview.setProperty("class", "mono")
        self.rtu_preview.setPlaceholderText(
            'Pick your RemoteConnect export, then "Assign addresses & '
            'preview".')
        lay.addWidget(self.rtu_preview, stretch=1)

        self.rtu_result = QLabel("")
        self.rtu_result.setWordWrap(True)
        self.rtu_result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.rtu_result)
        self._rtu_mode_changed(0)
        return page

    def _rtu_mode_changed(self, index: int) -> None:
        scanner = index == 1
        self.rtu_device_label.setVisible(scanner)
        self.rtu_device_edit.setVisible(scanner)
        self._t2_plc = None
        if hasattr(self, "rtu_generate_btn"):
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
        self._commit_selection()
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
            self._log(f"RTU assign failed: {exc}")
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
        self._log(f"RTU assign (Logic Editor mode): {pv.created} new objects.")
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
        self._log(f"RTU assign (scanner mode, device '{t2.device.name}'): "
                  f"{t2.created_objects} objects, {len(t2.new_blocks)} "
                  f"blocks, {len(t2.bind_rows)} bindings.")
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

    def _on_transferred(self, report) -> None:
        self.rtu_generate_btn.setEnabled(True)
        rc = report.rc
        lines = [
            f"1. {rc.xls_path}  ({rc.created} new objects; {rc.existing} "
            "already in the workbook, untouched)",
            f"2. {rc.st_path}",
            f"3. {rc.map_path}",
            f"4. {report.xsy_path}  ({report.xsy_removed} generated mirror "
            "variables removed)",
            f"5. {report.sections_dir}\\  ({len(report.section_files)} "
            "section files)",
        ]
        if rc.geoscada_path:
            lines.append(f"6. {rc.geoscada_path}  (DNP3 point list for the "
                         "Geo SCADA engineer)")
        self._log("RTU transfer bundle complete:\n  " + "\n  ".join(lines))
        for w in report.warnings:
            self._log(f"  warning: {w}")
        self.rtu_result.setText(
            f"<b style='color:{GREEN}'>Bundle complete.</b> "
            "In RemoteConnect: import the .xls; in the Logic Editor import "
            "the .xsy, then the section files in numbered order; create a "
            "final ST section and paste the mirror .st; build. Object "
            "variables bind by name (values are the .value member).")
        self.statusBar().showMessage("Transfer bundle complete.")
        QMessageBox.information(
            self, "Transfer to RemoteConnect",
            "Wrote:\n  " + "\n  ".join(lines) +
            ("\n\nWarnings:\n" + "\n".join(report.warnings[:8])
             if report.warnings else ""))

    def _on_t2_transferred(self, apply_report, t2_report) -> None:
        self.rtu_generate_btn.setEnabled(True)
        if not apply_report.ok:
            self.rtu_result.setText(
                f"<b style='color:{RED}'>PLC mirror failed:</b> "
                f"{apply_report.error} — the workbook was NOT modified.")
            self._log(f"T2 FAILED at the PLC step: {apply_report.error}")
            QMessageBox.critical(
                self, "SCADAPack RTU",
                f"The PLC mirror step failed:\n{apply_report.error}\n\n"
                "The RemoteConnect workbook was NOT modified.")
            return
        lines = [
            f"1. {t2_report.xls_path}  ({t2_report.created_objects} new "
            f"objects, {t2_report.new_blocks} scan blocks, "
            f"{t2_report.new_bindings} bindings on '{t2_report.device}')",
            f"2. {t2_report.map_path}",
        ]
        if t2_report.geoscada_path:
            lines.append(f"3. {t2_report.geoscada_path}  (DNP3 point list "
                         "for the Geo SCADA engineer)")
        self._log(
            f"T2 complete. PLC: {len(apply_report.created_vars)} vars "
            f"created, build {apply_report.build_state}.\n  "
            + "\n  ".join(lines))
        warnings = list(apply_report.warnings) + list(t2_report.warnings)
        for w in warnings:
            self._log(f"  warning: {w}")
        self.rtu_result.setText(
            f"<b style='color:{GREEN}'>T2 bundle complete.</b> Import the .xls "
            "in RemoteConnect and write to the device — the scanner starts "
            "polling the PLC; no Logic Editor program is needed. Point the "
            "HMI at the RtuRegister/HmiAddress column, GeoSCADA at the DNP3 "
            "points.")
        self.statusBar().showMessage("T2 bundle complete.")
        QMessageBox.information(
            self, "Transfer to RemoteConnect (T2)",
            f"PLC project updated ({len(apply_report.created_vars)} mirror "
            f"variables, build {apply_report.build_state}, saved).\n\n"
            "Wrote:\n  " + "\n  ".join(lines) +
            ("\n\nWarnings:\n" + "\n".join(warnings[:8]) if warnings else ""))

    # ------------------------------------------------------------- plumbing

    def _on_progress(self, msg: str) -> None:
        self.statusBar().showMessage(msg)
        self._log(msg)

    def _on_failed(self, tb: str) -> None:
        self.open_btn.setEnabled(True)
        self.generate_btn.setEnabled(self.plan is not None)
        self.rtu_generate_btn.setEnabled(False)
        self._log("ERROR:\n" + tb[-1200:])
        QMessageBox.critical(self, "DDT Mirror — error", tb[-2000:])
        self.statusBar().showMessage("Error — see dialog / activity log.")

    def closeEvent(self, event) -> None:
        self.request_shutdown.emit()
        self._thread.quit()
        self._thread.wait(15_000)
        super().closeEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    apply_theme(app)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
