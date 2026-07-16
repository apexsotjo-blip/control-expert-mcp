import csv
import io

import pytest
import xlrd
import xlwt

from ddt_mirror.codegen.remoteconnect import (
    COL_DATA_TYPE, COL_DNP3_GROUP, COL_DNP3_POINT, COL_GROUPING,
    COL_LOGIC_TYPE, COL_MODBUS_REG, COL_MODBUS_TYPE, COL_NAME, COL_SEQ,
    OBJECTS_SHEET, export_remoteconnect, read_workbook_index,
)
from ddt_mirror.core.engine import ProjectData
from ddt_mirror.core.flatten import flatten_tags
from ddt_mirror.core.persist import SidecarState
from ddt_mirror.core.rtu import GROUP_AI, GROUP_BI


def _make_workbook(path, objects):
    """Minimal RemoteConnect-shaped export: settings sheet + Objects sheet
    with the two header rows and the given (name, group, point, register)
    object rows."""
    wb = xlwt.Workbook()
    ws = wb.add_sheet("(0) Export Settings")
    ws.write(0, 0, "Id"); ws.write(0, 1, "Label"); ws.write(0, 2, "Value")
    ws.write(1, 0, "ExportFileVersion"); ws.write(1, 2, "v3.0")
    ws = wb.add_sheet(OBJECTS_SHEET)
    ws.write(0, COL_NAME, "Name - Do not modify an existing Name")
    ws.write(1, COL_NAME, "ObjName")
    for i, (name, group, point, register) in enumerate(objects):
        r = 2 + i
        ws.write(r, 0, "On <1>")
        ws.write(r, COL_SEQ, i)
        ws.write(r, COL_NAME, name)
        ws.write(r, COL_DNP3_GROUP, group)
        if point is not None:
            ws.write(r, COL_DNP3_POINT, point)
        if register is not None:
            ws.write(r, COL_MODBUS_REG, register)
    wb.save(str(path))


def _project(parsed) -> ProjectData:
    types, tags = parsed
    leaves, warnings = flatten_tags(tags, types)
    return ProjectData(types=types, tags=tags, leaves=leaves,
                       warnings=warnings)


def _state() -> SidecarState:
    state = SidecarState()
    state.selected_types = ["PUMP_T", "INT", "BOOL", "REAL"]
    return state


def _read_objects(path):
    sheet = xlrd.open_workbook(str(path)).sheet_by_name(OBJECTS_SHEET)
    rows = []
    for r in range(2, sheet.nrows):
        rows.append({c: sheet.cell_value(r, c) for c in range(sheet.ncols)})
    return rows


def test_read_workbook_index(tmp_path):
    src = tmp_path / "rtu.xls"
    _make_workbook(src, [
        ("Existing_DI", GROUP_BI, 4, None),
        ("Existing_AI", GROUP_AI, 9, 40003),
    ])
    idx = read_workbook_index(str(src))
    assert idx.names_lower == {"existing_di", "existing_ai"}
    assert idx.used_points == {"g1": {4}, "g30": {9}}
    assert idx.used_registers == {40003}
    assert idx.max_seq == 1


def test_missing_objects_sheet_rejected(tmp_path):
    bad = tmp_path / "notrtu.xls"
    wb = xlwt.Workbook()
    wb.add_sheet("Sheet1").write(0, 0, "x")
    wb.save(str(bad))
    with pytest.raises(ValueError, match="Objects"):
        read_workbook_index(str(bad))


def test_export_round_trip(tmp_path, parsed):
    src = tmp_path / "rtu.xls"
    _make_workbook(src, [("Existing_DI", GROUP_BI, 4, None)])
    stu = tmp_path / "plant.stu"
    stu.write_bytes(b"")
    data, state = _project(parsed), _state()

    report = export_remoteconnect(data, state, str(src), str(tmp_path),
                                  str(stu), timestamp="t0")
    assert report.ok and report.created > 0 and report.existing == 0

    rows = _read_objects(report.xls_path)
    by_name = {r[COL_NAME]: r for r in rows}
    assert "Existing_DI" in by_name  # original config preserved
    cmd = by_name["Pump1_Cmd"]
    assert cmd[COL_DATA_TYPE] == "Digital <1>"
    assert cmd[COL_LOGIC_TYPE] == "T_SPx70_BOOL <11>"
    assert cmd[COL_MODBUS_TYPE] == "Discrete <1>"
    assert cmd[COL_DNP3_POINT] == 5      # above the workbook's g1 point 4
    assert cmd[COL_GROUPING] == "Pump1"
    flow = by_name["Pump1_Flow_PV"]
    assert flow[COL_LOGIC_TYPE] == "T_SPx70_REAL <5>"
    assert flow[COL_MODBUS_REG] >= 40001
    # sequence ids continue after the existing rows
    seqs = [r[COL_SEQ] for r in rows]
    assert len(set(seqs)) == len(seqs)

    # ST references object .value members, never addresses
    assert "Pump1_Flow_PV.value := Pump1.Flow_PV;" in report.st_text
    assert "%MW" not in report.st_text

    # map CSV lists every assignment as new
    with open(report.map_path, encoding="utf-8-sig") as fh:
        map_rows = list(csv.DictReader(fh))
    assert all(r["Status"] == "new" for r in map_rows)
    assert state.rtu.entries  # committed to the sidecar


def test_export_rerun_is_idempotent(tmp_path, parsed):
    src = tmp_path / "rtu.xls"
    _make_workbook(src, [("Existing_DI", GROUP_BI, 4, None)])
    stu = tmp_path / "plant.stu"
    stu.write_bytes(b"")
    data, state = _project(parsed), _state()

    first = export_remoteconnect(data, state, str(src), str(tmp_path),
                                 str(stu), timestamp="t0")
    # engineer imported our file and re-exported: round two uses the
    # ENRICHED workbook as source
    second = export_remoteconnect(data, state, first.xls_path,
                                  str(tmp_path), str(stu), timestamp="t0")
    assert second.created == 0
    assert second.existing == first.created
    assert second.st_text == first.st_text
    rows1, rows2 = _read_objects(first.xls_path), _read_objects(second.xls_path)
    assert [r[COL_NAME] for r in rows1] == [r[COL_NAME] for r in rows2]


def test_hmi_address_index_base():
    from ddt_mirror.codegen.remoteconnect import hmi_address

    assert hmi_address(40001, 0) == "%MW0"
    assert hmi_address(40001, 1) == "%MW1"
    assert hmi_address(40510, 0) == "%MW509"
    assert hmi_address(1, 0) == "%M0"
    assert hmi_address(1, 1) == "%M1"
    assert hmi_address(None, 0) == ""


def test_map_csv_hmi_address_follows_setting(tmp_path, parsed):
    src = tmp_path / "rtu.xls"
    _make_workbook(src, [])
    stu = tmp_path / "plant.stu"
    stu.write_bytes(b"")
    data, state = _project(parsed), _state()
    state.settings.hmi_index_base = 1
    report = export_remoteconnect(data, state, str(src), str(tmp_path),
                                  str(stu), timestamp="t0")
    with open(report.map_path, encoding="utf-8-sig") as fh:
        rows = {r["ObjectName"]: r for r in csv.DictReader(fh)}
    flow = rows["Pump1_Flow_PV"]
    assert int(flow["ModbusRegister"]) == 40001
    assert flow["HmiAddress"] == "%MW1"       # 1-based per the setting
    cmd = rows["Pump1_Cmd"]
    assert cmd["HmiAddress"] == "%M1"


def test_source_duplicates_reported(tmp_path, parsed):
    src = tmp_path / "rtu.xls"
    _make_workbook(src, [
        ("Dup_A", GROUP_AI, 9, 40003),
        ("Dup_B", GROUP_AI, 9, 40003),   # engineer's pre-existing duplicate
    ])
    stu = tmp_path / "plant.stu"
    stu.write_bytes(b"")
    report = export_remoteconnect(_project(parsed), _state(), str(src),
                                  str(tmp_path), str(stu), timestamp="t0")
    assert any("duplicated addresses" in w for w in report.warnings)


def test_copy_preserves_empty_string_cells(tmp_path):
    """RemoteConnect stores absent parameters as empty-STRING cells and its
    importer logs 'Invalid parameter ... default value has been used' when
    they arrive as blank/missing cells instead (xlwt's write('') pitfall)."""
    from ddt_mirror.codegen.remoteconnect import write_workbook_copy

    src, dst = tmp_path / "in.xls", tmp_path / "out.xls"
    wb = xlwt.Workbook()
    ws = wb.add_sheet(OBJECTS_SHEET)
    ws.write(0, 0, "hdr")
    ws.row(1).set_cell_text(0, "")   # empty-string cell
    ws.write(1, 1, 42)
    wb.save(str(src))
    write_workbook_copy(str(src), str(dst), [])
    sheet = xlrd.open_workbook(str(dst)).sheet_by_name(OBJECTS_SHEET)
    assert sheet.cell(1, 0).ctype == xlrd.XL_CELL_TEXT
    assert sheet.cell(1, 0).value == ""
    assert sheet.cell(1, 1).value == 42


def test_export_renames_around_foreign_object(tmp_path, parsed):
    src = tmp_path / "rtu.xls"
    _make_workbook(src, [("Pump1_Cmd", GROUP_BI, 1, None)])  # foreign clash
    stu = tmp_path / "plant.stu"
    stu.write_bytes(b"")
    report = export_remoteconnect(_project(parsed), _state(), str(src),
                                  str(tmp_path), str(stu), timestamp="t0")
    names = {r[COL_NAME] for r in _read_objects(report.xls_path)}
    assert "Pump1_Cmd_2" in names
    assert "Pump1.Cmd := Pump1_Cmd_2.value;" in report.st_text
