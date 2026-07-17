"""T2 topology: the Modicon PLC runs the program; the SCADAPack polls it
over Modbus and re-serves the values to the HMI (RTU Modbus server) and
GeoSCADA (DNP3).

Everything is expressed in the RemoteConnect export workbook, per the
reference site export (SCADAPack 474, RC v3.0):

- '(9) Modbus Server Devices'  - the PLC as a remote Modbus/TCP device
  (must already exist in the engineer's export; we never invent device
  comms parameters).
- '(10) Modbus Scanners'       - contiguous register-block scans per
  device: Read blocks for statuses, Read/Write blocks for commands.
  Only the probed scan data type is emitted: 'UINT (Analog) <5000>'.
- '(14) Modbus Scanner - Obj'  - per-register binding of a scanned PLC
  register to an RTU object (sheet 2, by ObjSequenceId).
- '(2) Objects'                - scanner-fed objects: Analog <0>, Logic
  Variable Type 'None <0>' (no Logic Editor binding), own DNP3 point +
  RTU server register (site pattern, e.g. MBUS_*_UINT_45392).

The PLC side is the normal %M/%MW mirror generated with word_bools=True,
so every leaf (including BOOLs, as 0/1 words) is register-reachable.
PLC %MWn maps to 5-digit register 40001+n.

Idempotency without ownership marks: registers already covered by an
existing scan block (ours or the engineer's) get no new block; bindings
already present are not duplicated; objects reuse the sidecar's stable
names, and rows for objects already in the workbook are never re-appended.
"""

from __future__ import annotations

import copy as _copy
import csv
import io
import os
from dataclasses import dataclass, field

import xlrd

from ..core.engine import ProjectData, select_leaves
from ..core.model import Assignment
from ..core.persist import SidecarState, save_sidecar
from ..core.rtu import RtuAssignment, allocate_rtu, scanner_spec
from .remoteconnect import (
    _cell, build_object_row, hmi_address, read_workbook_index,
    write_workbook_copy,
)

SERVER_DEVICES_SHEET = "(9) Modbus Server Devices"
SCANNERS_SHEET = "(10) Modbus Scanners"
SCANNER_OBJ_SHEET = "(14) Modbus Scanner - Obj"
_HEADER_ROWS = 2

OP_READ = "Read <0>"
OP_RW = "Read/Write <2>"
SCAN_DATA_TYPE = "UINT (Analog) <5000>"  # the only import-probed scan type

READ_GAP_TOLERANCE = 8  # merge read runs across small gaps (harmless reads)


@dataclass
class ScanDevice:
    seq: int
    name: str
    register_addressing: str  # "5 Digits <0>" in the reference


@dataclass
class ScanBlock:
    scanner_seq: int
    device_seq: int
    operation: str  # OP_READ / OP_RW
    start: int      # 5-digit register
    quantity: int

    def covers(self, reg: int) -> bool:
        return self.start <= reg < self.start + self.quantity


@dataclass
class ScannerIndex:
    devices: dict[str, ScanDevice] = field(default_factory=dict)  # lower name
    blocks: list[ScanBlock] = field(default_factory=list)
    max_scanner_seq: int = 0
    # (register, obj_seq) pairs already bound on sheet 14
    bound: set[tuple[int, int]] = field(default_factory=set)


def read_scanner_index(path: str) -> ScannerIndex:
    book = xlrd.open_workbook(path, on_demand=True)
    idx = ScannerIndex()
    names = book.sheet_names()
    if SERVER_DEVICES_SHEET in names:
        sh = book.sheet_by_name(SERVER_DEVICES_SHEET)
        for r in range(_HEADER_ROWS, sh.nrows):
            name = str(_cell(sh, r, 1)).strip()
            seq = _cell(sh, r, 0)
            if name and isinstance(seq, (int, float)):
                idx.devices[name.lower()] = ScanDevice(
                    int(seq), name, str(_cell(sh, r, 7)).strip())
    if SCANNERS_SHEET in names:
        sh = book.sheet_by_name(SCANNERS_SHEET)
        for r in range(_HEADER_ROWS, sh.nrows):
            dev, start, qty, sseq = (_cell(sh, r, 0), _cell(sh, r, 7),
                                     _cell(sh, r, 8), _cell(sh, r, 12))
            if not isinstance(sseq, (int, float)):
                continue
            idx.max_scanner_seq = max(idx.max_scanner_seq, int(sseq))
            if isinstance(dev, (int, float)) and isinstance(start, (int, float)):
                idx.blocks.append(ScanBlock(
                    int(sseq), int(dev), str(_cell(sh, r, 2)).strip(),
                    int(start), int(qty or 0)))
    if SCANNER_OBJ_SHEET in names:
        sh = book.sheet_by_name(SCANNER_OBJ_SHEET)
        for r in range(_HEADER_ROWS, sh.nrows):
            reg, obj = _cell(sh, r, 1), _cell(sh, r, 3)
            if isinstance(reg, (int, float)) and isinstance(obj, (int, float)):
                idx.bound.add((int(reg), int(obj)))
    book.release_resources()
    return idx


def plc_register(address: str) -> int | None:
    """CE located address -> 5-digit Modbus register on the PLC.
    %MWn -> holding 40001+n (Schneider: %MWn is wire address n).
    %Mn  -> coil n+1 (not scannable with the probed enum - handled by
    word_bools upstream; returned for completeness)."""
    a = address.strip().upper()
    if a.startswith("%MW"):
        return 40001 + int(a[3:])
    if a.startswith("%M") and a[2:].isdigit():
        return int(a[2:]) + 1
    return None


def build_scan_blocks(
    device: ScanDevice,
    wanted: dict[int, str],           # register -> "read" | "rw"
    existing: list[ScanBlock],
    next_scanner_seq: int,
) -> tuple[list[ScanBlock], list[str]]:
    """Blocks to add so every wanted register is covered.

    Read tags are satisfied by ANY covering block (a Read/Write block also
    reads); Read/Write tags require a Read/Write block. New read runs are
    merged across gaps of up to READ_GAP_TOLERANCE unused registers
    (reading a few extra registers is harmless); write blocks are strictly
    contiguous so the RTU never WRITES a register we do not own."""
    dev_blocks = [b for b in existing if b.device_seq == device.seq]

    def covered(reg: int, need_write: bool) -> bool:
        for b in dev_blocks:
            if b.covers(reg) and (b.operation == OP_RW or not need_write):
                return True
        return False

    new_blocks: list[ScanBlock] = []
    warnings: list[str] = []
    seq = next_scanner_seq

    def runs(regs: list[int], gap: int) -> list[tuple[int, int]]:
        out: list[tuple[int, int]] = []
        for r in sorted(regs):
            if out and r <= out[-1][0] + out[-1][1] - 1 + 1 + gap:
                start, qty = out[-1]
                out[-1] = (start, r - start + 1)
            else:
                out.append((r, 1))
        return out

    rw_regs = [r for r, op in wanted.items()
               if op == "rw" and not covered(r, True)]
    rd_regs = [r for r, op in wanted.items()
               if op == "read" and not covered(r, False)]

    for start, qty in runs(rw_regs, gap=0):
        new_blocks.append(ScanBlock(seq, device.seq, OP_RW, start, qty))
        seq += 1
    for start, qty in runs(rd_regs, gap=READ_GAP_TOLERANCE):
        new_blocks.append(ScanBlock(seq, device.seq, OP_READ, start, qty))
        seq += 1
    if len(new_blocks) > 8:
        warnings.append(
            f"{len(new_blocks)} new scan blocks were needed (fragmented "
            "PLC addressing) - consider consolidating scan ranges in "
            "RemoteConnect if the RTU's scanner limit is reached")
    return new_blocks, warnings


def scanner_row(b: ScanBlock, device: ScanDevice) -> dict[int, object]:
    row: dict[int, object] = {
        0: device.seq, 1: device.name, 2: b.operation,
        6: SCAN_DATA_TYPE, 7: b.start, 8: b.quantity,
        9: "Analog <0>", 10: 0, 11: 0, 12: b.scanner_seq,
        13: "Disabled <0>",
    }
    if b.operation == OP_RW:
        row.update({3: "On change <2>", 4: "Send read requests <0>",
                    5: "Revert point values <0>"})
    else:
        row.update({3: "", 4: "", 5: ""})
    return row


def binding_row(scanner_seq: int, register: int,
                obj_seq: int) -> dict[int, object]:
    return {0: scanner_seq, 1: register, 2: 0, 3: obj_seq}


# ------------------------------------------------------------------- export

@dataclass
class T2Report:
    ok: bool = False
    xls_path: str = ""
    map_path: str = ""
    device: str = ""
    created_objects: int = 0
    existing_objects: int = 0
    new_blocks: int = 0
    new_bindings: int = 0
    sidecar_path: str = ""
    warnings: list[str] = field(default_factory=list)


def generate_t2_map_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["PlcPath", "PlcAddress", "PlcRegister", "ObjectName",
                     "Access", "Dnp3Group", "Dnp3Point", "RtuRegister",
                     "HmiAddress", "Status"])
    for r in rows:
        writer.writerow([r[k] for k in
                         ("path", "plc_address", "plc_register", "object",
                          "access", "group", "point", "rtu_register",
                          "hmi_address", "status")])
    return buf.getvalue()


@dataclass
class T2Plan:
    device: ScanDevice | None = None
    rtu: object = None              # RtuAllocState copy to commit
    obj_rows: list[dict] = field(default_factory=list)
    new_blocks: list[ScanBlock] = field(default_factory=list)
    bind_rows: list[dict] = field(default_factory=list)
    map_rows: list[dict] = field(default_factory=list)
    created_objects: int = 0
    existing_objects: int = 0
    warnings: list[str] = field(default_factory=list)


def plan_scanner(
    data: ProjectData,
    state: SidecarState,
    plc_assignments: list[Assignment],
    src_xls: str,
    device_name: str,
) -> T2Plan:
    """Pure T2 planning against the engineer's export: no files written,
    no state mutated (works on a copy of the sidecar's RTU state)."""
    plan = T2Plan()
    idx = read_workbook_index(src_xls)
    sidx = read_scanner_index(src_xls)

    device = sidx.devices.get(device_name.strip().lower())
    if device is None:
        available = ", ".join(sorted(d.name for d in sidx.devices.values()))
        raise ValueError(
            f"Modbus server device '{device_name}' is not in the workbook. "
            f"Configure the PLC as a Modbus/TCP device in RemoteConnect "
            f"first, then re-export. Devices present: [{available or 'none'}]")
    plan.device = device
    if device.register_addressing and "5 digits" not in \
            device.register_addressing.lower():
        plan.warnings.append(
            f"device '{device.name}' uses register addressing "
            f"'{device.register_addressing}' - the generated registers "
            "assume '5 Digits'; verify before importing")

    # PLC address per selected leaf (word_bools plan: everything but WORD2
    # is a single register)
    plc_by_path = {a.leaf.full_path: a for a in plc_assignments}

    leaves = select_leaves(data, state)
    rtu = _copy.deepcopy(state.rtu)
    ours = rtu.reserved_names()
    assignments, warnings = allocate_rtu(
        rtu, leaves, idx.names_lower - ours, idx.used_points,
        idx.used_registers,
        point_floor=idx.n_objects + 1,
        workbook_names=idx.names_lower,
        foreign_points=idx.foreign_points(ours),
        foreign_registers=idx.foreign_registers(ours),
        spec_fn=scanner_spec)
    plan.rtu = rtu
    plan.warnings = list(data.warnings) + warnings + plan.warnings

    # objects to append + sequence ids (existing keep theirs, new get fresh)
    to_append = [a for a in assignments
                 if a.entry.name.lower() not in idx.names_lower]
    plan.created_objects = len(to_append)
    plan.existing_objects = len(assignments) - len(to_append)
    seq_of: dict[str, int] = dict(idx.seq_by_name)
    obj_rows = []
    next_seq = idx.max_seq + 1
    for a in to_append:
        # build_object_row already emits Task 'MAST <0>' - the probed
        # pattern for scanner-fed objects too
        obj_rows.append(build_object_row(a, next_seq))
        seq_of[a.entry.name.lower()] = next_seq
        next_seq += 1

    # wanted PLC registers per access
    wanted: dict[int, str] = {}
    reg_of_path: dict[str, int] = {}
    for a in assignments:
        plc = plc_by_path.get(a.leaf.full_path)
        if plc is None:
            plan.warnings.append(
                f"{a.leaf.full_path}: no PLC mirror address - leaf skipped "
                "in scanner mapping (regenerate the PLC mirror first)")
            continue
        reg = plc_register(plc.address)
        if reg is None or reg < 40001:
            plan.warnings.append(
                f"{a.leaf.full_path}: PLC address {plc.address} is not a "
                "scannable holding register - skipped")
            continue
        wanted[reg] = ("rw" if a.entry.access == "read_write" else "read")
        reg_of_path[a.leaf.full_path] = reg

    new_blocks, block_warnings = build_scan_blocks(
        device, wanted, sidx.blocks, sidx.max_scanner_seq + 1)
    plan.warnings.extend(block_warnings)
    plan.new_blocks = new_blocks
    all_blocks = sidx.blocks + new_blocks

    def covering_block(reg: int, need_write: bool) -> ScanBlock | None:
        for b in all_blocks:
            if (b.device_seq == device.seq and b.covers(reg)
                    and (b.operation == OP_RW or not need_write)):
                return b
        return None

    bind_rows = []
    map_rows = []
    for a in assignments:
        reg = reg_of_path.get(a.leaf.full_path)
        status = "existing" if a.entry.name.lower() in idx.names_lower \
            else "new"
        if reg is not None:
            obj_seq = seq_of.get(a.entry.name.lower())
            block = covering_block(reg, wanted.get(reg) == "rw")
            if obj_seq is None or block is None:
                plan.warnings.append(
                    f"{a.leaf.full_path}: internal - no object seq/scan "
                    f"block for register {reg}")
            elif (reg, obj_seq) not in sidx.bound:
                bind_rows.append(binding_row(block.scanner_seq, reg, obj_seq))
        map_rows.append({
            "path": a.leaf.full_path,
            "plc_address": plc_by_path[a.leaf.full_path].address
            if a.leaf.full_path in plc_by_path else "",
            "plc_register": reg if reg is not None else "",
            "object": a.entry.name,
            "access": "R/W" if a.entry.access == "read_write" else "R",
            "group": a.entry.group,
            "point": a.entry.dnp3_point if a.entry.dnp3_point is not None
            else "",
            "rtu_register": a.entry.register
            if a.entry.register is not None else "",
            "hmi_address": hmi_address(a.entry.register,
                                       state.settings.hmi_index_base),
            "status": status,
        })
    plan.obj_rows = obj_rows
    plan.bind_rows = bind_rows
    plan.map_rows = map_rows
    return plan


def export_scanner_bundle(
    data: ProjectData,
    state: SidecarState,
    plc_assignments: list[Assignment],
    src_xls: str,
    out_dir: str,
    project_path: str,
    device_name: str,
) -> T2Report:
    """Enrich the engineer's RC export with scanner-fed objects for every
    PLC-mirrored leaf: object rows + scan blocks + register bindings.
    Commits the RTU allocation to the sidecar after the files are written.
    The PLC-side plan (word_bools=True) must already be applied to the CE
    project by the caller."""
    plan = plan_scanner(data, state, plc_assignments, src_xls, device_name)
    report = T2Report(
        device=plan.device.name,
        created_objects=plan.created_objects,
        existing_objects=plan.existing_objects,
        new_blocks=len(plan.new_blocks),
        new_bindings=len(plan.bind_rows),
        warnings=plan.warnings,
    )

    stem = os.path.splitext(os.path.basename(project_path))[0] or "ddt_mirror"
    report.xls_path = os.path.join(out_dir, f"{stem}_T2_import.xls")
    report.map_path = os.path.join(out_dir, f"{stem}_T2_point_map.csv")

    write_workbook_copy(src_xls, report.xls_path, plan.obj_rows, {
        SCANNERS_SHEET: [scanner_row(b, plan.device) for b in plan.new_blocks],
        SCANNER_OBJ_SHEET: plan.bind_rows,
    })
    with open(report.map_path, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(generate_t2_map_csv(plan.map_rows))

    state.rtu = plan.rtu
    report.sidecar_path = save_sidecar(project_path, state)
    report.ok = True
    return report
