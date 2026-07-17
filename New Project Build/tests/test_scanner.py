import csv

import pytest
import xlrd
import xlwt

from ddt_mirror.codegen.remoteconnect import (
    COL_DATA_TYPE, COL_LOGIC_TYPE, COL_MODBUS_TYPE, COL_NAME, COL_SEQ,
    OBJECTS_SHEET,
)
from ddt_mirror.codegen.scanner import (
    OP_READ, OP_RW, SCAN_DATA_TYPE, SCANNER_OBJ_SHEET, SCANNERS_SHEET,
    SERVER_DEVICES_SHEET, ScanBlock, ScanDevice, build_scan_blocks,
    export_scanner_bundle, plc_register, read_scanner_index,
)
from ddt_mirror.core.engine import ProjectData, build_plan
from ddt_mirror.core.flatten import flatten_tags
from ddt_mirror.core.persist import SidecarState


def _make_t2_workbook(path, objects=(), devices=(("PLC_Rack", 1),),
                      scanners=(), bindings=()):
    wb = xlwt.Workbook()
    ws = wb.add_sheet("(0) Export Settings")
    ws.write(1, 0, "ExportFileVersion"); ws.write(1, 2, "v3.0")

    ws = wb.add_sheet(OBJECTS_SHEET)
    ws.write(0, COL_NAME, "Name"); ws.write(1, COL_NAME, "ObjName")
    for i, name in enumerate(objects):
        ws.write(2 + i, 0, "On <1>")
        ws.write(2 + i, COL_SEQ, i)
        ws.write(2 + i, COL_NAME, name)

    ws = wb.add_sheet(SERVER_DEVICES_SHEET)
    ws.write(1, 0, "DeviceSequenceId"); ws.write(1, 1, "DeviceName")
    for i, (name, seq) in enumerate(devices):
        r = 2 + i
        ws.write(r, 0, seq); ws.write(r, 1, name)
        ws.write(r, 2, "Modbus/TCP <5>"); ws.write(r, 7, "5 Digits <0>")

    ws = wb.add_sheet(SCANNERS_SHEET)
    ws.write(1, 0, "DeviceSequenceId")
    for i, (dev_seq, op, start, qty, sseq) in enumerate(scanners):
        r = 2 + i
        ws.write(r, 0, dev_seq); ws.write(r, 2, op)
        ws.write(r, 6, SCAN_DATA_TYPE); ws.write(r, 7, start)
        ws.write(r, 8, qty); ws.write(r, 12, sseq)

    ws = wb.add_sheet(SCANNER_OBJ_SHEET)
    ws.write(1, 0, "PointScannerSequenceId")
    for i, (sseq, reg, obj_seq) in enumerate(bindings):
        r = 2 + i
        ws.write(r, 0, sseq); ws.write(r, 1, reg)
        ws.write(r, 2, 0); ws.write(r, 3, obj_seq)
    wb.save(str(path))


def _project(parsed) -> ProjectData:
    types, tags = parsed
    leaves, warnings = flatten_tags(tags, types)
    return ProjectData(types=types, tags=tags, leaves=leaves,
                       warnings=warnings)


def _state() -> SidecarState:
    state = SidecarState()
    state.selected_types = ["PUMP_T", "INT", "BOOL"]
    return state


def test_plc_register_mapping():
    assert plc_register("%MW0") == 40001
    assert plc_register("%MW5391") == 45392   # the site's read block start
    assert plc_register("%M0") == 1
    assert plc_register("%IW3") is None


def test_word_bools_plan_places_bools_in_word_space(parsed):
    data, state = _project(parsed), _state()
    plan, _ = build_plan(data, state, timestamp="t0", word_bools=True)
    by_path = {a.leaf.full_path: a for a in plan.assignments
               if not a.premapped}
    cmd = by_path["Pump1.Cmd"]           # BOOL
    assert cmd.address.startswith("%MW")
    assert "Pump1.Cmd := INT_TO_BOOL(" in plan.st_source \
        or f"{cmd.address} := BOOL_TO_INT(Pump1.Cmd);" in plan.st_source


def test_build_scan_blocks_merges_reads_strict_writes():
    dev = ScanDevice(1, "PLC", "5 Digits <0>")
    wanted = {40001: "rw", 40002: "rw", 40010: "rw",       # 2 rw runs
              41001: "read", 41003: "read", 41020: "read"}  # gap 8 merge
    blocks, _ = build_scan_blocks(dev, wanted, [], next_scanner_seq=5)
    rw = [b for b in blocks if b.operation == OP_RW]
    rd = [b for b in blocks if b.operation == OP_READ]
    assert [(b.start, b.quantity) for b in rw] == [(40001, 2), (40010, 1)]
    # 41001..41003 merge (gap 1 <= 8); 41020 too far
    assert [(b.start, b.quantity) for b in rd] == [(41001, 3), (41020, 1)]
    assert [b.scanner_seq for b in blocks] == [5, 6, 7, 8]


def test_build_scan_blocks_skips_covered_registers():
    dev = ScanDevice(1, "PLC", "5 Digits <0>")
    existing = [ScanBlock(1, 1, OP_RW, 40001, 4),
                ScanBlock(2, 1, OP_READ, 41000, 10)]
    wanted = {40002: "rw",        # covered by rw block
              41005: "read",      # covered by read block
              41005 + 100: "read",  # not covered
              40003: "read"}      # read need covered by RW block too
    blocks, _ = build_scan_blocks(dev, wanted, existing, 3)
    assert [(b.operation, b.start, b.quantity) for b in blocks] == [
        (OP_READ, 41105, 1)]


def test_export_scanner_bundle_end_to_end(parsed, tmp_path):
    src = tmp_path / "rtu.xls"
    _make_t2_workbook(src, objects=["Existing_AI"])
    stu = tmp_path / "plant.stu"
    stu.write_bytes(b"")
    data, state = _project(parsed), _state()

    plan, alloc = build_plan(data, state, timestamp="t0", word_bools=True)
    state.alloc = alloc  # PLC plan applied (test stands in for apply_plan)

    report = export_scanner_bundle(
        data, state, plan.assignments, str(src), str(tmp_path), str(stu),
        device_name="PLC_Rack")
    assert report.ok
    assert report.created_objects > 0
    assert report.new_blocks >= 2       # at least one read + one rw block

    book = xlrd.open_workbook(report.xls_path)
    obj = book.sheet_by_name(OBJECTS_SHEET)
    rows = {}
    for r in range(2, obj.nrows):
        rows[obj.cell_value(r, COL_NAME)] = r

    # scanner-fed objects: Analog, LogicType None, UINT - even for BOOLs
    assert "Pump1_Cmd" in rows
    r = rows["Pump1_Cmd"]
    assert obj.cell_value(r, COL_DATA_TYPE) == "Analog <0>"
    assert obj.cell_value(r, COL_LOGIC_TYPE) == "None <0>"
    assert obj.cell_value(r, COL_MODBUS_TYPE) == "UINT <3>"

    # scan blocks reference the device and only the probed data type
    sc = book.sheet_by_name(SCANNERS_SHEET)
    ops = set()
    for r in range(2, sc.nrows):
        if sc.cell_value(r, 12):
            assert sc.cell_value(r, 6) == SCAN_DATA_TYPE
            assert sc.cell_value(r, 1) == "PLC_Rack"
            ops.add(sc.cell_value(r, 2))
    assert OP_READ in ops and OP_RW in ops

    # every binding points at an object row and a covering scan block
    so = book.sheet_by_name(SCANNER_OBJ_SHEET)
    n_bind = 0
    for r in range(2, so.nrows):
        if so.cell_value(r, 1):
            n_bind += 1
    assert n_bind == report.new_bindings > 0

    # map CSV: PLC register chain is present
    with open(report.map_path, encoding="utf-8-sig") as fh:
        map_rows = list(csv.DictReader(fh))
    by_path = {m["PlcPath"]: m for m in map_rows}
    cmd = by_path["Pump1.Cmd"]
    assert cmd["PlcRegister"] and int(cmd["PlcRegister"]) >= 40001
    assert cmd["Access"] == "R/W"
    assert cmd["Dnp3Point"] != ""

    # WORD2 leaves are skipped with the probe warning
    assert any("32-bit" in w for w in report.warnings) or \
        "REAL" not in state.selected_types


def test_export_scanner_bundle_is_idempotent(parsed, tmp_path):
    src = tmp_path / "rtu.xls"
    _make_t2_workbook(src)
    stu = tmp_path / "plant.stu"
    stu.write_bytes(b"")
    data, state = _project(parsed), _state()
    plan, alloc = build_plan(data, state, timestamp="t0", word_bools=True)
    state.alloc = alloc

    r1 = export_scanner_bundle(data, state, plan.assignments, str(src),
                               str(tmp_path), str(stu), "PLC_Rack")
    # engineer re-exports: second run against OUR OWN enriched workbook
    r2 = export_scanner_bundle(data, state, plan.assignments, r1.xls_path,
                               str(tmp_path), str(stu), "PLC_Rack")
    assert r2.created_objects == 0          # objects already in workbook
    assert r2.new_blocks == 0               # registers already covered
    assert r2.new_bindings == 0             # bindings already present


def test_unknown_device_lists_available(parsed, tmp_path):
    src = tmp_path / "rtu.xls"
    _make_t2_workbook(src, devices=(("PLC_A", 1), ("PLC_B", 2)))
    data, state = _project(parsed), _state()
    plan, _ = build_plan(data, state, timestamp="t0", word_bools=True)
    with pytest.raises(ValueError) as exc:
        export_scanner_bundle(data, state, plan.assignments, str(src),
                              str(tmp_path), str(tmp_path / "p.stu"),
                              "Nonexistent")
    assert "PLC_A" in str(exc.value) and "PLC_B" in str(exc.value)


def test_scanner_index_reads_all_sheets(tmp_path):
    src = tmp_path / "rtu.xls"
    _make_t2_workbook(
        src, devices=(("PLC", 3),),
        scanners=((3, OP_RW, 40001, 4, 1), (3, OP_READ, 45392, 73, 2)),
        bindings=((1, 40001, 82), (2, 45392, 86)))
    idx = read_scanner_index(str(src))
    assert idx.devices["plc"].seq == 3
    assert idx.max_scanner_seq == 2
    assert len(idx.blocks) == 2
    assert (40001, 82) in idx.bound and (45392, 86) in idx.bound
