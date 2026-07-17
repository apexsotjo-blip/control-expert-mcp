"""RemoteConnect workbook round-trip: read the engineer's exported .xls,
inject our RTU objects into a copy, and emit the paste-ready ST section
plus a point-map CSV.

Flow (full-workbook round-trip, so nothing of the RTU config is lost):
1. Engineer exports the RTU configuration from RemoteConnect (.xls).
2. We index it: existing object names, used DNP3 point numbers per group,
   used Modbus registers, highest sequence id.
3. Objects are allocated (core.rtu) above everything in use, and rows for
   objects not already present are appended to the '(2) Objects' sheet of
   a value-faithful copy.
4. Engineer re-imports the copy in RemoteConnect ('Replace all
   configuration' is fine: the copy still contains the full config).

Row layout and every enum string were lifted from a real RemoteConnect
v3.0 export (SCADAPack 474, DTM 4.6) - see the templates below. The copy
preserves cell values (not formatting), which is what the importer reads.
"""

from __future__ import annotations

import copy as _copy
import csv
import io
import os
import re
from dataclasses import dataclass, field

import xlrd
import xlwt

from .. import __version__
from ..core.engine import ProjectData, select_leaves
from ..core.persist import SidecarState, save_sidecar
from ..core.rtu import (
    DT_ANALOG, DT_COUNTER, DT_DIGITAL, RtuAssignment, allocate_rtu,
    generate_rtu_st, group_space,
)

OBJECTS_SHEET = "(2) Objects"
PROJECT_SETTINGS_SHEET = "(1) Project Settings"
PARAMETERS_SHEET = "(19) Parameters"
_DEVICE_TYPE_ID = "RtuProjectSettingsDeviceType"
_DNP3_ADDRESS_ID = "RtuSettingsBasicGeneralRtuDnpAddress"

# Objects sheet columns (fixed layout of the v3.0 export)
COL_ENABLE = 0
COL_SEQ = 1
COL_LOCKED = 2
COL_NAME = 3
COL_DATA_TYPE = 4
COL_LOGIC_TYPE = 5
COL_TASK = 6
COL_COMMENT = 7
COL_GROUPING = 8
COL_DNP3_POINT = 9
COL_DNP3_GROUP = 10
COL_MODBUS_REG = 14
COL_MODBUS_TYPE = 15

_HEADER_ROWS = 2  # row 0 = labels, row 1 = ids, data starts at row 2

# Defaults shared by every object row in the reference export (columns
# 24..109); only the type-identity columns differ per data type.
_COMMON_DEFAULTS: dict[int, object] = {
    11: "Local <7>", 12: "Local <7>", 13: "Local <7>",
    21: "Disabled <0>",
    24: "Disabled <0>",
    25: 0, 26: 10000, 27: 0, 28: 100,
    29: "None <3>", 30: 100,
    31: "Disabled <0>", 32: 0, 33: 0, 34: 0, 35: "No <0>",
    39: "Disabled <0>", 40: 0, 41: 100, 42: 100, 43: 0, 44: 0,
    45: "Disabled <0>", 46: "Disabled <0>", 47: 0,
    48: "Disabled <0>", 49: "Disabled <0>", 50: 0,
    51: "Disabled <0>", 52: "Disabled <0>", 53: 0,
    54: "Disabled <0>", 55: "Disabled <0>", 56: 0,
    57: "Disabled <0>", 58: "Disabled <0>", 59: 0,
    60: "Disabled <0>", 61: "Disabled <0>", 62: 0,
    63: "Disabled <0>", 64: "Disabled <0>", 65: 0,
    66: "Disabled <0>", 67: "Disabled <0>", 68: 0,
    69: 0, 70: 0, 71: "No <0>", 72: 0, 73: 0,
    74: "Disabled <0>", 75: "No <0>", 76: "On <1>",
    77: 50, 78: 0, 79: "Disabled <0>", 80: "No <0>",
    81: 0, 82: 0, 83: 0, 84: 0, 85: 0,
    88: "Disabled <0>",
    90: "Enabled <1>", 91: "Enabled <1>", 92: "Enabled <1>",
    93: "Enabled <1>", 94: "Enabled <1>", 95: "Enabled <1>",
    96: "Enabled <1>", 97: "Enabled <1>", 98: "Enabled <1>",
    99: "Enabled <1>",
    100: "Enabled <1>", 101: "Enabled <1>", 102: "Enabled <1>",
    103: "Enabled <1>", 104: "Enabled <1>",
    105: 0, 106: 0, 107: 15,
    108: "Disabled <0>", 109: "None <-1>",
}

# IEC 60870-5-104 columns (19-23) per data type, as in the reference.
_IEC104 = {
    DT_DIGITAL: {19: 0, 20: "1-Sgl Pt Info <1>", 22: 0,
                 23: "45-Sgl Cmd <45>"},
    DT_ANALOG: {19: 0, 20: "13-Msrd Val SFl <13>", 22: 0,
                23: "50-Set Pt Cmd SFl <50>"},
    DT_COUNTER: {19: 0, 20: "15-Int Tot <15>", 22: 0, 23: "None <0>"},
}

_CODE_RE = re.compile(r"<(-?\d+)>\s*$")


@dataclass
class WorkbookIndex:
    path: str
    names_lower: set[str] = field(default_factory=set)
    seq_by_name: dict[str, int] = field(default_factory=dict)  # lower name
    used_points: dict[str, set[int]] = field(default_factory=dict)
    used_registers: set[int] = field(default_factory=set)
    # per-row usage, for foreign-collision and duplicate detection
    point_by_name: dict[str, tuple[str, int]] = field(default_factory=dict)
    register_by_name: dict[str, int] = field(default_factory=dict)
    max_seq: int = -1
    n_objects: int = 0
    device_type: str = ""  # RC 'Device Type' label, e.g. 'SCADAPack 474 <61>'
    dnp3_address: object = ""  # the RTU's own DNP3 outstation address

    def foreign_points(self, ours: set[str]) -> dict[str, set[int]]:
        out: dict[str, set[int]] = {}
        for name, (space, point) in self.point_by_name.items():
            if name not in ours:
                out.setdefault(space, set()).add(point)
        return out

    def foreign_registers(self, ours: set[str]) -> set[int]:
        return {r for n, r in self.register_by_name.items() if n not in ours}

    def duplicates(self) -> list[str]:
        """Addresses already duplicated inside the source itself."""
        seen: dict[tuple, str] = {}
        dups: list[str] = []
        for name, (space, point) in sorted(self.point_by_name.items()):
            key = ("point", space, point)
            if key in seen:
                dups.append(f"DNP3 {space} point {point}: '{seen[key]}' and "
                            f"'{name}'")
            else:
                seen[key] = name
        for name, reg in sorted(self.register_by_name.items()):
            key = ("reg", reg)
            if key in seen:
                dups.append(f"Modbus register {reg}: '{seen[key]}' and "
                            f"'{name}'")
            else:
                seen[key] = name
        return dups


def _cell(sheet, r: int, c: int):
    """Cell value, '' when the row is shorter than c (xlrd rows are ragged)."""
    return sheet.cell_value(r, c) if c < sheet.row_len(r) else ""


def read_workbook_index(path: str) -> WorkbookIndex:
    book = xlrd.open_workbook(path, on_demand=True)
    if OBJECTS_SHEET not in book.sheet_names():
        raise ValueError(
            f"'{os.path.basename(path)}' has no '{OBJECTS_SHEET}' sheet - "
            "export the RTU configuration from RemoteConnect (Device > "
            "Export) and pick that file.")
    sheet = book.sheet_by_name(OBJECTS_SHEET)
    idx = WorkbookIndex(path=path)
    for r in range(_HEADER_ROWS, sheet.nrows):
        name = str(_cell(sheet, r, COL_NAME)).strip()
        if not name:
            continue
        idx.n_objects += 1
        idx.names_lower.add(name.lower())
        seq = _cell(sheet, r, COL_SEQ)
        if isinstance(seq, (int, float)) and not isinstance(seq, bool):
            idx.max_seq = max(idx.max_seq, int(seq))
            idx.seq_by_name[name.lower()] = int(seq)
        point = _cell(sheet, r, COL_DNP3_POINT)
        if isinstance(point, (int, float)) and not isinstance(point, bool):
            space = group_space(str(_cell(sheet, r, COL_DNP3_GROUP)))
            idx.used_points.setdefault(space, set()).add(int(point))
            idx.point_by_name[name.lower()] = (space, int(point))
        reg = _cell(sheet, r, COL_MODBUS_REG)
        if isinstance(reg, (int, float)) and not isinstance(reg, bool):
            idx.used_registers.add(int(reg))
            idx.register_by_name[name.lower()] = int(reg)
    if PROJECT_SETTINGS_SHEET in book.sheet_names():
        ps = book.sheet_by_name(PROJECT_SETTINGS_SHEET)
        for r in range(ps.nrows):
            if str(_cell(ps, r, 0)).strip() == _DEVICE_TYPE_ID:
                idx.device_type = str(_cell(ps, r, 2)).strip()
                break
    if PARAMETERS_SHEET in book.sheet_names():
        ps = book.sheet_by_name(PARAMETERS_SHEET)
        for r in range(ps.nrows):
            if str(_cell(ps, r, 0)).strip() == _DNP3_ADDRESS_ID:
                v = _cell(ps, r, 2)
                if isinstance(v, float) and v.is_integer():
                    v = int(v)
                idx.dnp3_address = v
                break
    book.release_resources()
    return idx


def build_object_row(a: RtuAssignment, seq_id: int) -> dict[int, object]:
    """Column -> value for one appended Objects row."""
    cells: dict[int, object] = dict(_COMMON_DEFAULTS)
    cells.update(_IEC104[a.spec.data_type])
    cells.update({
        COL_ENABLE: "On <1>",
        COL_SEQ: seq_id,
        COL_LOCKED: False,  # boolean cell, like the native export
        COL_NAME: a.entry.name,
        COL_DATA_TYPE: a.spec.data_type,
        COL_LOGIC_TYPE: a.spec.logic_type,
        COL_TASK: "MAST <0>",
        COL_COMMENT: a.leaf.comment or f"DDT Mirror: {a.leaf.full_path}",
        COL_GROUPING: a.leaf.instance,
        COL_DNP3_GROUP: a.entry.group,
        COL_MODBUS_TYPE: a.spec.modbus_type,
    })
    if a.entry.dnp3_point is not None:
        cells[COL_DNP3_POINT] = a.entry.dnp3_point
    if a.entry.register is not None:
        cells[COL_MODBUS_REG] = a.entry.register
    return cells


def write_workbook_copy(src_path: str, dst_path: str,
                        new_rows: list[dict[int, object]],
                        extra_sheet_rows: dict[str, list[dict[int, object]]]
                        | None = None) -> None:
    """Value-faithful copy of the workbook with rows appended to the
    Objects sheet (and, via `extra_sheet_rows`, to any other sheet by
    name). Formatting is not preserved (the importer reads values)."""
    append = dict(extra_sheet_rows or {})
    if new_rows:
        append.setdefault(OBJECTS_SHEET, [])
        append[OBJECTS_SHEET] = list(new_rows) + append[OBJECTS_SHEET]
    book = xlrd.open_workbook(src_path)
    out = xlwt.Workbook()
    for sheet in book.sheets():
        ws = out.add_sheet(sheet.name)
        for r in range(sheet.nrows):
            for c in range(sheet.row_len(r)):
                cell = sheet.cell(r, c)
                if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                    continue
                value = cell.value
                if cell.ctype == xlrd.XL_CELL_TEXT and value == "":
                    # ws.write("") silently emits a BLANK cell, but the
                    # RemoteConnect importer distinguishes empty-string
                    # cells ("explicitly none") from missing ones (it logs
                    # 'Invalid parameter ... default value has been used')
                    ws.row(r).set_cell_text(c, "")
                    continue
                if cell.ctype == xlrd.XL_CELL_BOOLEAN:
                    # e.g. 'Object Locked'; xlwt.write() would demote a
                    # Python bool to a number cell
                    ws.row(r).set_cell_boolean(c, int(value))
                    continue
                if (cell.ctype == xlrd.XL_CELL_NUMBER
                        and float(value).is_integer()):
                    value = int(value)
                ws.write(r, c, value)
        rows_to_add = append.get(sheet.name)
        if rows_to_add:
            row = sheet.nrows
            for cells in rows_to_add:
                for c, value in cells.items():
                    if isinstance(value, bool):
                        ws.row(row).set_cell_boolean(c, int(value))
                    elif value == "":
                        ws.row(row).set_cell_text(c, "")
                    else:
                        ws.write(row, c, value)
                row += 1
    # drop the mmap on the source before saving: dst may BE src (re-export
    # of an already-enriched workbook) and Windows refuses to reopen it
    book.release_resources()
    out.save(dst_path)


def hmi_address(register: int | None, index_base: int) -> str:
    """HMI-side IEC address for an RTU Modbus server register.

    RTU register 40001 / coil 1 is Modbus wire address 0, so an HMI polling
    the RTU with IEC61131 syntax sees %MW0 / %M0 (index_base 0, the Modbus
    standard). index_base 1 shifts by one for servers/drivers that map
    40001 to wire address 1."""
    if register is None:
        return ""
    if register >= 40001:
        return f"%MW{register - 40001 + index_base}"
    return f"%M{register - 1 + index_base}"


def generate_map_csv(assignments: list[RtuAssignment], new_names: set[str],
                     index_base: int = 0) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["PlcPath", "ObjectName", "CeType", "DataType",
                     "LogicVariableType", "Access", "Dnp3Group",
                     "Dnp3Point", "ModbusRegister", "HmiAddress", "Status"])
    for a in assignments:
        writer.writerow([
            a.leaf.full_path, a.entry.name, a.entry.ce_type,
            a.entry.data_type, a.entry.logic_type,
            "R/W" if a.entry.access == "read_write" else "R",
            a.entry.group,
            "" if a.entry.dnp3_point is None else a.entry.dnp3_point,
            "" if a.entry.register is None else a.entry.register,
            hmi_address(a.entry.register, index_base),
            "new" if a.entry.name.lower() in new_names else "existing",
        ])
    return buf.getvalue()


@dataclass
class RcReport:
    ok: bool = False
    xls_path: str = ""
    st_path: str = ""
    map_path: str = ""
    geoscada_path: str = ""  # DNP3 point list for the SCADA engineer
    sidecar_path: str = ""
    created: int = 0
    existing: int = 0
    warnings: list[str] = field(default_factory=list)
    st_text: str = ""
    device_type: str = ""  # RC 'Device Type' label from the source workbook


def allocate_against_workbook(data: ProjectData, state: SidecarState,
                              src_xls: str):
    """Assign RTU objects/DNP3 points/Modbus registers against the engineer's
    exported workbook. Pure: works on a COPY of the sidecar's RTU state and
    never writes. Returns (rtu_copy, workbook_index, assignments, warnings).

    This is the moment addresses are actually decided — deliberately AFTER
    the workbook is provided, because points/registers are allocated
    relative to everything already in it (RTU addressing has no meaning
    before the source export is known)."""
    leaves = select_leaves(data, state)
    idx = read_workbook_index(src_xls)
    rtu = _copy.deepcopy(state.rtu)
    ours = rtu.reserved_names()
    foreign = idx.names_lower - ours
    assignments, warnings = allocate_rtu(
        rtu, leaves, foreign, idx.used_points, idx.used_registers,
        program_names={t.name for t in data.tags},
        # import-probed: explicit DNP3 points collide with RemoteConnect's
        # internal auto-assignment unless numbered above every object it
        # could have auto-assigned - i.e. above the total object count
        point_floor=idx.n_objects + 1,
        workbook_names=idx.names_lower,
        foreign_points=idx.foreign_points(ours),
        foreign_registers=idx.foreign_registers(ours))
    warnings = list(data.warnings) + warnings
    dups = idx.duplicates()
    if dups:
        sample = "; ".join(dups[:5]) + (" ..." if len(dups) > 5 else "")
        warnings.append(
            f"the source workbook already contains {len(dups)} duplicated "
            f"addresses among its own objects (left as-is): [{sample}]")
    return rtu, idx, assignments, warnings


@dataclass
class RcPreview:
    device_type: str = ""
    created: int = 0
    existing: int = 0
    map_csv: str = ""
    warnings: list[str] = field(default_factory=list)


def preview_remoteconnect(data: ProjectData, state: SidecarState,
                          src_xls: str) -> RcPreview:
    """Compute (without writing anything or mutating state) the RTU address
    assignment for review: the point/register/object map + new-vs-existing
    counts + warnings."""
    _rtu, idx, assignments, warnings = allocate_against_workbook(
        data, state, src_xls)
    new_names = {a.entry.name.lower() for a in assignments
                 if a.entry.name.lower() not in idx.names_lower}
    return RcPreview(
        device_type=idx.device_type,
        created=len(new_names),
        existing=len(assignments) - len(new_names),
        map_csv=generate_map_csv(assignments, new_names,
                                 state.settings.hmi_index_base),
        warnings=warnings,
    )


def export_remoteconnect(
    data: ProjectData,
    state: SidecarState,
    src_xls: str,
    out_dir: str,
    project_path: str,
    timestamp: str = "",
) -> RcReport:
    """Full export: allocate against the workbook, write the .xls copy,
    the ST section and the map CSV, then commit the allocation to the
    sidecar. State is only mutated after every file is written."""
    report = RcReport()
    rtu, idx, assignments, warnings = allocate_against_workbook(
        data, state, src_xls)
    report.device_type = idx.device_type
    report.warnings = warnings

    to_append = [a for a in assignments
                 if a.entry.name.lower() not in idx.names_lower]
    new_names = {a.entry.name.lower() for a in to_append}
    report.created = len(to_append)
    report.existing = len(assignments) - len(to_append)

    seq = idx.max_seq + 1
    rows = []
    for a in to_append:
        rows.append(build_object_row(a, seq))
        seq += 1

    stem = os.path.splitext(os.path.basename(project_path))[0] or "ddt_mirror"
    report.xls_path = os.path.join(out_dir, f"{stem}_RTU_import.xls")
    report.st_path = os.path.join(out_dir, f"{stem}_RTU_mirror.st")
    report.map_path = os.path.join(out_dir, f"{stem}_RTU_point_map.csv")

    write_workbook_copy(src_xls, report.xls_path, rows)
    report.st_text = generate_rtu_st(
        assignments, project=os.path.basename(project_path),
        version=__version__, timestamp=timestamp)
    with open(report.st_path, "w", encoding="utf-8") as fh:
        fh.write(report.st_text)
    with open(report.map_path, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(generate_map_csv(assignments, new_names,
                                  state.settings.hmi_index_base))

    if any(a.entry.dnp3_point is not None for a in assignments):
        from .geoscada import generate_geoscada_csv

        report.geoscada_path = os.path.join(
            out_dir, f"{stem}_GeoSCADA_dnp3_points.csv")
        with open(report.geoscada_path, "w", encoding="utf-8-sig",
                  newline="") as fh:
            fh.write(generate_geoscada_csv(assignments, idx.dnp3_address))

    state.rtu = rtu
    report.sidecar_path = save_sidecar(project_path, state)
    report.ok = True
    return report
