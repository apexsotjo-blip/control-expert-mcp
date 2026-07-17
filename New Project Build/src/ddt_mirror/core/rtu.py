"""SCADAPack RTU (RemoteConnect) mirror planning.

RTU objects bind to Logic Editor variables BY NAME, not by address: an
object whose Logic Variable Type is a T_SPx70_* type appears in the
RemoteConnect Logic Editor as a variable of the object's name. Those
variables are STRUCTURES - the process value is their .value member
(confirmed in the Logic Editor 2026-07-15). The mirror is therefore one
object per selected leaf plus a paste-ready ST section copying
Tag.Member <-> object.value (the engineer authors the program in Control
Expert and re-creates it in the Logic Editor; our section goes with it).

DNP3 point numbers and RTU Modbus server registers follow the same
stability contract as the %MW allocator: append-only, tombstoned, never
reshuffled - and additionally never colliding with numbers already used in
the engineer's RemoteConnect export workbook (SCADA point lists and HMI
register maps must survive regeneration).

All enum strings below were lifted from a real RemoteConnect v3.0 export
(SCADAPack 474, DTM 4.6) - do not invent new '<N>' codes without probing
an import first.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from .model import Access, FlatLeaf

RTU_MAX_NAME = 32

# ------------------------------------------------- RemoteConnect enum values

DT_DIGITAL = "Digital <1>"
DT_ANALOG = "Analog <0>"
DT_COUNTER = "Counter <2>"

LVT_NONE = "None <0>"  # scanner-fed objects: no Logic Editor binding
LVT_BOOL = "T_SPx70_BOOL <11>"
LVT_INT = "T_SPx70_INT <2>"
LVT_DINT = "T_SPx70_DINT <4>"
LVT_UDINT = "T_SPx70_UDINT <6>"
LVT_REAL = "T_SPx70_REAL <5>"

GROUP_BI = "g1v1 Binary Input No Flags <257>"
GROUP_AI = "g30v1 32b Int AI Flags <286>"
GROUP_AO = "g40v1 32b Int AO Flags <296>"
GROUP_COUNTER = "g20v1 32-bit Counter with Flags <276>"

MB_DISCRETE = "Discrete <1>"
MB_UINT = "UINT <3>"

# RTU Modbus server address ranges ('5 Digits' register addressing)
COIL_FIRST, COIL_LAST = 1, 9999
HOLDING_FIRST, HOLDING_LAST = 40001, 49999

_GROUP_CODE = re.compile(r"<(\d+)>\s*$")


def group_space(group: str) -> str:
    """DNP3 point-number space of a group enum: numbers are unique per
    group type ('g1', 'g30', 'g40', 'g20'), not globally."""
    m = _GROUP_CODE.search(group)
    return f"g{int(m.group(1)) - 256}" if m else group


# ------------------------------------------------------- CE type -> RTU spec

@dataclass(frozen=True)
class RtuSpec:
    data_type: str      # DT_* enum
    logic_type: str     # LVT_* enum
    group: str          # GROUP_* enum
    modbus_type: str    # MB_* enum
    reg_space: str      # "coil" | "holding"
    conv_to_tag: str = ""  # ST conversion wrapping the object on Tag := ...
    conv_to_obj: str = ""  # ST conversion wrapping the tag on Obj := ...


def rtu_spec(leaf: FlatLeaf) -> tuple[RtuSpec | None, str]:
    """Spec for one leaf; (None, category) when unsupported. The second
    element is a warning-category key ('' = clean) aggregated by caller."""
    t = leaf.type_name.strip().upper()
    rw = leaf.access is Access.READ_WRITE
    if t in ("BOOL", "EBOOL"):
        return RtuSpec(DT_DIGITAL, LVT_BOOL, GROUP_BI, MB_DISCRETE, "coil"), \
            ("rw_digital_dnp3" if rw else "")
    if t == "INT":
        return RtuSpec(DT_ANALOG, LVT_INT, GROUP_AO if rw else GROUP_AI,
                       MB_UINT, "holding"), ""
    if t == "UINT":
        return RtuSpec(DT_ANALOG, LVT_INT, GROUP_AO if rw else GROUP_AI,
                       MB_UINT, "holding",
                       conv_to_tag="INT_TO_UINT", conv_to_obj="UINT_TO_INT"), \
            "uint_as_int"
    if t == "WORD":
        return RtuSpec(DT_ANALOG, LVT_INT, GROUP_AO if rw else GROUP_AI,
                       MB_UINT, "holding",
                       conv_to_tag="INT_TO_WORD", conv_to_obj="WORD_TO_INT"), \
            "word_as_int"
    if t == "DINT":
        return RtuSpec(DT_ANALOG, LVT_DINT, GROUP_AO if rw else GROUP_AI,
                       MB_UINT, "holding"), ""
    if t == "UDINT":
        if rw:
            # Counter objects are read-only accumulators; a writable UDINT
            # has no sample precedent - Analog + T_SPx70_UDINT is our best
            # guess and needs an import probe.
            return RtuSpec(DT_ANALOG, LVT_UDINT, GROUP_AO, MB_UINT,
                           "holding"), "rw_udint"
        return RtuSpec(DT_COUNTER, LVT_UDINT, GROUP_COUNTER, MB_UINT,
                       "holding"), ""
    if t == "DWORD":
        return RtuSpec(DT_ANALOG, LVT_UDINT, GROUP_AO if rw else GROUP_AI,
                       MB_UINT, "holding",
                       conv_to_tag="UDINT_TO_DWORD",
                       conv_to_obj="DWORD_TO_UDINT"), "dword"
    if t == "REAL":
        return RtuSpec(DT_ANALOG, LVT_REAL, GROUP_AO if rw else GROUP_AI,
                       MB_UINT, "holding"), ""
    return None, "unsupported"


def scanner_spec(leaf: FlatLeaf) -> tuple[RtuSpec | None, str]:
    """Spec for a SCANNER-FED object (T2: the PLC runs the program and the
    SCADAPack polls it): no Logic Editor binding (Logic Variable Type
    'None <0>'), served as Analog UINT — the only scan data type present
    in the reference site export ('UINT (Analog) <5000>' blocks feeding
    Analog/None<0> objects). BOOLs arrive as 0/1 words (the PLC mirror is
    generated with word_bools=True); 32-bit types need an unprobed scan
    enum and are skipped."""
    from .model import LeafKind

    rw = leaf.access is Access.READ_WRITE
    if leaf.kind is LeafKind.WORD2:
        return None, "t2_word2"
    spec = RtuSpec(DT_ANALOG, LVT_NONE, GROUP_AO if rw else GROUP_AI,
                   MB_UINT, "holding")
    if leaf.kind is LeafKind.BIT:
        return spec, "t2_bool_analog"
    return spec, ""


_WARNING_TEXT = {
    "uint_as_int": (
        "UINT leaves map to T_SPx70_INT objects with INT_TO_UINT/UINT_TO_INT "
        "copies (no T_SPx70_UINT exists); values > 32767 read negative on "
        "SCADA/HMI"),
    "word_as_int": (
        "WORD leaves map to T_SPx70_INT objects with INT_TO_WORD/WORD_TO_INT "
        "copies"),
    "rw_udint": (
        "Read/Write UDINT leaves become Analog T_SPx70_UDINT objects (g40 AO)"
        " - combination not present in the reference export, verify the "
        "import accepts it"),
    "dword": (
        "DWORD leaves map to T_SPx70_UDINT objects with UDINT_TO_DWORD/"
        "DWORD_TO_UDINT copies - verify the import accepts them"),
    "rw_digital_dnp3": (
        "Read/Write digitals are exported as g1v1 Binary Input; SCADA "
        "writes over DNP3 need a Binary Output group set manually in "
        "RemoteConnect (enum code unknown - needs a probe). Writes over the "
        "RTU Modbus server coil still work"),
    "unsupported": (
        "leaves with no SCADAPack object equivalent were skipped "
        "(TIME/DATE/STRING/...)"),
    "t2_word2": (
        "32-bit leaves (REAL/DINT/...) cannot be served over the Modbus "
        "scanner yet - the 32-bit scan data-type enum is unprobed; they "
        "were skipped (HMI can still read them from the PLC directly)"),
    "t2_bool_analog": (
        "BOOL leaves are mirrored to %MW words and served as Analog 0/1 "
        "objects (probed-safe); native Digital objects on scanned "
        "register bits need an import probe"),
}


# --------------------------------------------------------- allocation state

@dataclass
class RtuEntry:
    name: str
    data_type: str
    logic_type: str
    group: str
    access: str
    kind: str            # LeafKind value, to detect drift
    ce_type: str         # CE elementary type when allocated
    dnp3_point: int | None = None
    register: int | None = None
    direct: bool = False  # object name == program variable, no ST copy
    active: bool = True

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @staticmethod
    def from_dict(d: dict) -> "RtuEntry":
        e = RtuEntry(d["name"], d["data_type"], d["logic_type"], d["group"],
                     d["access"], d.get("kind", ""), d.get("ce_type", ""))
        e.dnp3_point = d.get("dnp3_point")
        e.register = d.get("register")
        e.direct = bool(d.get("direct", False))
        e.active = bool(d.get("active", True))
        return e


@dataclass
class RtuAllocState:
    entries: dict[str, RtuEntry] = field(default_factory=dict)  # key = full_path
    dead: list[dict] = field(default_factory=list)  # superseded, never reused

    def to_dict(self) -> dict:
        return {"entries": {k: e.to_dict() for k, e in self.entries.items()},
                "dead": self.dead}

    @staticmethod
    def from_dict(d: dict) -> "RtuAllocState":
        return RtuAllocState(
            entries={k: RtuEntry.from_dict(e)
                     for k, e in d.get("entries", {}).items()},
            dead=list(d.get("dead", [])),
        )

    def reserved_names(self) -> set[str]:
        """Lower-cased names owned by this sidecar, dead slots included
        (their objects may still exist in the RTU)."""
        names = {e.name.lower() for e in self.entries.values()}
        names.update(str(d.get("name", "")).lower() for d in self.dead)
        return names


@dataclass
class RtuAssignment:
    leaf: FlatLeaf
    entry: RtuEntry
    spec: RtuSpec


def object_name(full_path: str) -> str:
    """RTU object name from a CE path: dots to underscores, <= 32 chars
    (hash-suffixed beyond that), letter-first, no doubled/trailing
    underscores - same identifier rules the Logic Editor inherits from CE."""
    name = re.sub(r"[^A-Za-z0-9_]", "_", full_path)
    name = re.sub(r"_+", "_", name).strip("_") or "X"
    if name[0].isdigit():
        name = "N" + name
    if len(name) > RTU_MAX_NAME:
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:4].upper()
        name = name[: RTU_MAX_NAME - 5].rstrip("_") + "_" + digest
    return name


def _dedupe(candidate: str, taken: set[str]) -> str:
    if candidate.lower() not in taken:
        return candidate
    base, i = candidate, 2
    while True:
        suffix = f"_{i}"
        name = base[: RTU_MAX_NAME - len(suffix)].rstrip("_") + suffix
        if name.lower() not in taken:
            return name
        i += 1


def allocate_rtu(
    state: RtuAllocState,
    leaves: list[FlatLeaf],
    foreign_names: set[str],
    used_points: dict[str, set[int]],
    used_registers: set[int],
    program_names: set[str] | None = None,
    point_floor: int = 0,
    workbook_names: set[str] | None = None,
    foreign_points: dict[str, set[int]] | None = None,
    foreign_registers: set[int] | None = None,
    spec_fn=rtu_spec,
) -> tuple[list[RtuAssignment], list[str]]:
    """Assign object names / DNP3 points / Modbus registers, mutating state
    append-only. `foreign_names` (lower-cased), `used_points` (per space)
    and `used_registers` describe what the engineer's workbook already uses
    outside our sidecar - we allocate above ALL of it and never reuse.

    `program_names` are the CE project's top-level variable names: every
    generated object name must dodge them, because importing an object
    auto-creates a Logic Editor variable of the same name (a T_SPx70_*
    STRUCT, value in .value) which would clash with the program variable.
    Standalone tags therefore get a '_Obj' suffix.

    `point_floor` (pass total-workbook-object-count + 1): RemoteConnect
    auto-assigns DNP3 point numbers to objects whose export shows none, and
    the import REJECTS rows whose explicit point collides with an
    auto-assigned one (import-probed: points above the total object count
    import cleanly, low points vanish). All new points start at the floor.
    Sidecar points below the floor whose object is absent from
    `workbook_names` never landed in the RTU (a rejected import) and are
    renumbered; points on objects present in the workbook are live and are
    never touched.

    `foreign_points`/`foreign_registers` are the numbers used by workbook
    rows we do NOT own (duplicate-address handling): a sidecar entry whose
    number now collides with a foreign object is renumbered when our object
    never landed in the RTU, or reported as a live duplicate to resolve in
    RemoteConnect when both objects exist."""
    warnings: list[str] = []
    categories: dict[str, list[str]] = {}
    program = {n.lower() for n in (program_names or set())}
    in_workbook = {n.lower() for n in (workbook_names or set())}

    taken = ({n.lower() for n in foreign_names} | state.reserved_names()
             | program)

    # numbering floors: everything the workbook or any of our slots
    # (active, tombstoned or dead) ever used
    next_point: dict[str, int] = {}
    for space, nums in used_points.items():
        next_point[space] = max(nums, default=0) + 1
    reg_floor = {"coil": COIL_FIRST, "holding": HOLDING_FIRST}
    reg_last = {"coil": COIL_LAST, "holding": HOLDING_LAST}
    next_reg = dict(reg_floor)
    for r in used_registers:
        for sp in ("coil", "holding"):
            if reg_floor[sp] <= r <= reg_last[sp]:
                next_reg[sp] = max(next_reg[sp], r + 1)

    def note(entry: RtuEntry | dict) -> None:
        point = entry.dnp3_point if isinstance(entry, RtuEntry) else entry.get("dnp3_point")
        group = entry.group if isinstance(entry, RtuEntry) else entry.get("group", "")
        reg = entry.register if isinstance(entry, RtuEntry) else entry.get("register")
        if point is not None:
            space = group_space(group)
            next_point[space] = max(next_point.get(space, 1), point + 1)
        if reg is not None:
            for sp in ("coil", "holding"):
                if reg_floor[sp] <= reg <= reg_last[sp]:
                    next_reg[sp] = max(next_reg[sp], reg + 1)

    for e in state.entries.values():
        note(e)
    for d in state.dead:
        note(d)

    def new_point(space: str) -> int:
        point = max(next_point.get(space, 1), point_floor)
        next_point[space] = point + 1
        return point

    def new_register(space: str) -> int | None:
        reg = next_reg[space]
        if reg > reg_last[space]:
            return None
        next_reg[space] = reg + 1
        return reg

    # remediation: sidecar numbers that never landed in the RTU (rejected
    # import) and are now unsafe - below the floor, or colliding with a
    # foreign object's number - get fresh ones; live duplicates (both
    # objects exist in the workbook) can only be fixed in RemoteConnect.
    f_points = foreign_points or {}
    f_regs = foreign_registers or set()
    renumbered = 0
    live_dups: list[str] = []
    for key, entry in state.entries.items():
        landed = entry.name.lower() in in_workbook
        space = group_space(entry.group)
        if entry.dnp3_point is not None:
            unsafe = (entry.dnp3_point < point_floor
                      or entry.dnp3_point in f_points.get(space, set()))
            if unsafe and not landed:
                entry.dnp3_point = new_point(space)
                renumbered += 1
            elif landed and entry.dnp3_point in f_points.get(space, set()):
                live_dups.append(f"{entry.name} (DNP3 {space} point "
                                 f"{entry.dnp3_point})")
        if entry.register is not None:
            reg_space = "coil" if entry.register <= COIL_LAST else "holding"
            if entry.register in f_regs:
                if not landed:
                    entry.register = new_register(reg_space)
                    renumbered += 1
                    if entry.register is None:
                        warnings.append(
                            f"{entry.name}: RTU Modbus {reg_space} range "
                            "exhausted while renumbering - object left "
                            "without a register")
                else:
                    live_dups.append(f"{entry.name} (Modbus register "
                                     f"{entry.register})")
    if renumbered:
        warnings.append(
            f"{renumbered} DNP3 points/registers from a previous run were "
            "unsafe (below the collision floor or now duplicated by another "
            "object) and their objects are not in the workbook - renumbered")
    if live_dups:
        sample = ", ".join(live_dups[:5]) + (" ..." if len(live_dups) > 5 else "")
        warnings.append(
            f"{len(live_dups)} of our live objects share an address with "
            f"another object in the workbook - resolve in RemoteConnect "
            f"(ours are never renumbered once imported): [{sample}]")

    assignments: list[RtuAssignment] = []
    selected_keys: set[str] = set()
    retired: list[str] = []

    for leaf in leaves:
        spec, category = spec_fn(leaf)
        if category:
            categories.setdefault(category, []).append(leaf.full_path)
        if spec is None:
            continue
        key = leaf.full_path
        selected_keys.add(key)

        entry = state.entries.get(key)
        if entry and (entry.kind != leaf.kind.value
                      or entry.access != leaf.access.value
                      or entry.logic_type != spec.logic_type
                      or entry.direct):
            # (direct binding was abandoned: T_SPx70_* are STRUCTS in the
            # Logic Editor - the value is the .value member - so an object
            # can never share a program variable's name.) The RTU may still
            # hold the old object under the old name - retire the slot and
            # give the replacement a fresh name.
            state.dead.append({"key": key, **entry.to_dict()})
            retired.append(entry.name)
            entry = None
        if entry is None:
            # standalone tags: the object may not take the tag's own name
            # (its auto-created Logic Editor variable would clash with the
            # program variable), hence the _Obj suffix
            base = key + "_Obj" if not leaf.rel_path else key
            name = _dedupe(object_name(base), taken)
            taken.add(name.lower())
            point = new_point(group_space(spec.group))
            reg = new_register(spec.reg_space)
            if reg is None:
                warnings.append(
                    f"{key}: RTU Modbus {spec.reg_space} range exhausted - "
                    "object exported without a register")
            entry = RtuEntry(
                name=name, data_type=spec.data_type,
                logic_type=spec.logic_type, group=spec.group,
                access=leaf.access.value, kind=leaf.kind.value,
                ce_type=leaf.type_name.strip().upper(),
                dnp3_point=point, register=reg)
            state.entries[key] = entry
        entry.active = True
        assignments.append(RtuAssignment(leaf=leaf, entry=entry, spec=spec))

    for key, entry in state.entries.items():
        if key not in selected_keys:
            entry.active = False

    if retired:
        sample = ", ".join(retired[:5]) + (" ..." if len(retired) > 5 else "")
        warnings.append(
            f"{len(retired)} objects retired (type/access changed or stale "
            f"direct binding) - the OLD objects stay in the RTU and can be "
            f"deleted manually in RemoteConnect: [{sample}]")

    for category, paths in sorted(categories.items()):
        sample = ", ".join(paths[:3]) + (" ..." if len(paths) > 3 else "")
        warnings.append(
            f"{len(paths)} {_WARNING_TEXT[category]} [{sample}]")
    if any(a.entry.logic_type in (LVT_REAL, LVT_DINT, LVT_UDINT)
           for a in assignments):
        warnings.append(
            "REAL/32-bit objects are served as g30/g40 32-bit integers on "
            "DNP3 and 16-bit UINT registers on the RTU Modbus server "
            "(reference-export behaviour); REAL fractions are lost there - "
            "use raw/engineering scaling in RemoteConnect if needed")
    return assignments, warnings


# ------------------------------------------------------------- ST generation

RTU_HEADER_TEMPLATE = """\
(* ==============================================================
   GENERATED BY DDT MIRROR v{version} - DO NOT EDIT BY HAND.
   Paste as an ST section (task MAST) in the RemoteConnect Logic
   Editor, AFTER importing the generated .xls (which creates the
   RTU objects these names bind to) and re-creating your Control
   Expert program there.
   T_SPx70_* object variables are structures - the process value
   is their .value member.
   Project: {project}
   Generated: {timestamp}
   Read/Write tags (SCADA/HMI writes) are copied object -> tag first;
   Read tags (displays) are copied tag -> object last.
   ============================================================== *)
"""


def _rtu_copy_line(a: RtuAssignment) -> str:
    obj = f"{a.entry.name}.value"
    if a.leaf.access is Access.READ_WRITE:
        source = (f"{a.spec.conv_to_tag}({obj})"
                  if a.spec.conv_to_tag else obj)
        return f"{a.leaf.full_path} := {source};"
    source = (f"{a.spec.conv_to_obj}({a.leaf.full_path})"
              if a.spec.conv_to_obj else a.leaf.full_path)
    return f"{obj} := {source};"


def generate_rtu_st(
    assignments: list[RtuAssignment],
    project: str = "",
    version: str = "0.1.0",
    timestamp: str = "",
) -> str:
    lines = [RTU_HEADER_TEMPLATE.format(
        version=version, project=project or "-", timestamp=timestamp or "-")]
    rw = [a for a in assignments if a.leaf.access is Access.READ_WRITE]
    rd = [a for a in assignments if a.leaf.access is Access.READ]
    lines.append("(* --- SCADA/HMI -> RTU : Read/Write tags (commands / setpoints) --- *)")
    lines.extend(_rtu_copy_line(a) for a in rw)
    lines.append("")
    lines.append("(* --- RTU -> SCADA/HMI : Read tags (statuses / process values) --- *)")
    lines.extend(_rtu_copy_line(a) for a in rd)
    lines.append("")
    return "\n".join(lines)
