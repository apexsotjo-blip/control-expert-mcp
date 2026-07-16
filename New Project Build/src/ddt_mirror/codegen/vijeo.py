"""Vijeo Designer import files, matching the user's real export samples
(HMI_SAMPLES/): a .VJDDataTypes UDT file and a variables CSV.

File 1 — UDT definitions (<typeList> XML, extension .VJDDataTypes):
one <structure> per CE DDT, named '<DDT>_UDT', numeric typeID; nested DDT
members reference the child's typeID. Dependencies are emitted first.

File 2 — variables CSV ('5.1.0, Vijeo-Designer 6.2.11 CSV output' banner):
- Folder rows: one folder per UDT, nested folders mirror DDT nesting.
- DDTVariable + SubVariable rows: each CE DDT instance keeps its CE name and
  full member tree; SubVariable device addresses are the app's allocated
  %M/%MW mirrors (integer types carry BIN/Signed/DataLength format columns,
  as in the sample).
- Per UDT folder: one Internal STRING '<DDT>_Popup' + one InternalReference
  variable per elementary member ('%s.<member>(<folder>.<DDT>_Popup  )') —
  the generic-popup faceplate pattern. Point the popup string at an instance
  path at runtime (e.g. 'Pump1' for the Pump folder, 'Pump1.Ctrl' for a
  nested folder).

Type-level member exclusions drop the field from the UDT (when excluded from
every usage). Per-variable exclusions keep the field but leave that one
SubVariable unaddressed (warned).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

from ..core.model import DdtType, MirrorPlan, Tag, leaf_kind
from ..core.persist import SidecarState

VIJEO_MAX_NAME = 32


def safe_vijeo_name(raw: str, max_len: int = VIJEO_MAX_NAME) -> str:
    """Make a CE identifier legal for Vijeo: letter-first, single underscores,
    no leading/trailing underscore, <= 32 chars (leading '_' names like the
    CE DDT '_AI' crash Vijeo's UDT import outright)."""
    name = re.sub(r"[^A-Za-z0-9_]", "_", raw)
    name = re.sub(r"_+", "_", name).strip("_") or "X"
    if name[0].isdigit():
        name = "N" + name
    if len(name) > max_len:
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:4].upper()
        name = name[: max_len - 5].rstrip("_") + "_" + digest
    return name


class NameScope:
    """Dedupes sanitized names within one Vijeo namespace; warns on rename."""

    def __init__(self, warnings: list[str], what: str) -> None:
        self._used: set[str] = set()
        self._warnings = warnings
        self._what = what

    def add(self, raw: str, max_len: int = VIJEO_MAX_NAME) -> str:
        name = safe_vijeo_name(raw, max_len)
        base, i = name, 2
        while name.lower() in self._used:
            suffix = f"_{i}"
            name = base[: max_len - len(suffix)].rstrip("_") + suffix
            i += 1
        self._used.add(name.lower())
        if name != raw:
            self._warnings.append(
                f"{self._what} '{raw}' renamed to '{name}' for Vijeo")
        return name

# CE elementary type -> Vijeo data type (InputAddress\Unity_Pro.htm table).
VIJEO_TYPE = {
    "BOOL": "BOOL", "EBOOL": "BOOL",
    "INT": "INT", "UINT": "UINT", "WORD": "UINT",
    "DINT": "DINT", "UDINT": "UDINT", "DWORD": "UDINT", "TIME": "UDINT",
    "REAL": "REAL",
}

# Data Format / Signed / Data Length CSV columns, keyed by the VIJEO type the
# row declares (sample shows DINT as BIN/2sComplement/32Bits; REAL and BOOL
# leave them empty).
_INT_FORMAT = {
    "INT": ("BIN", "2sComplement", "16Bits"),
    "UINT": ("BIN", "Unsigned", "16Bits"),
    "DINT": ("BIN", "2sComplement", "32Bits"),
    "UDINT": ("BIN", "Unsigned", "32Bits"),
}

CSV_BANNER = "'5.1.0, Vijeo-Designer 6.2.11 CSV output"
CSV_COLUMNS = [
    "Type", "Name", "Data Type", "Data Source", "Dimension", "Description",
    "Initial Value", "NumofBytes", "Data Sharing", "Alarm", "Language1 ID",
    "Alarm Message", "Alarm Type", "Trigger Condition", "Deadband", "Target",
    "LoLo\\Lo\\Hi\\HiHi", "Minor", "Major", "Alarm Group", "Severity",
    "Vibration Pattern", "Vibration Time", "Sound File", "Play Mode",
    "Scan Group", "Device Address", "Bit Number", "Data Format", "Signed",
    "Data Length", "Offset Bit No", "Bit Width", "InputRange", "Min", "Max",
    "DataScaling", "RawMin", "RawMax", "ScaledMin", "ScaledMax",
    "IndirectEnabled", "IndirectAddress", "Retentive", "LoggingGroup",
    "LogUserOperationsOnVariable",
]

POPUP_BYTES = 32  # STRING size for popup instance-path holders


def _quote(text: str) -> str:
    return '"' + (text or "").replace('"', '""') + '"'


def _desc(text: str) -> str:
    """Description cell. Vijeo's CSV import does NOT honor quoted commas
    (a comma inside a description shifts every following column and the
    whole DDT element block is rejected at its first row), so commas become
    semicolons; quotes and line breaks are neutralized the same way."""
    clean = (text or "").replace('"', "'").replace(",", ";")
    clean = " ".join(clean.split())
    return f'"{clean}"'


def _row(**cells: str) -> str:
    """One CSV row with every header column, plus the sample's trailing comma."""
    values = [cells.get(col, "") for col in CSV_COLUMNS]
    return ",".join(values) + ","


# ----------------------------------------------------------------- UDT model

@dataclass
class UdtSpec:
    ddt: str                 # CE DDT name
    base: str = ""           # sanitized Vijeo base name (also the folder name)
    type_id: int = 0
    # (ce member, vijeo member, comment, vijeo type) in CE order
    elementary: list[tuple[str, str, str, str]] = field(default_factory=list)
    # (ce member, vijeo member, child ddt)
    structs: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def udt_name(self) -> str:
        return safe_vijeo_name(f"{self.base}_UDT")

    def vijeo_member(self, ce_member: str) -> str | None:
        for ce, vj, _c, _t in self.elementary:
            if ce == ce_member:
                return vj
        for ce, vj, _child in self.structs:
            if ce == ce_member:
                return vj
        return None


def _collect_included(
    types: dict[str, DdtType], top_ddts: list[str], excluded: set[str],
) -> set[tuple[str, str]]:
    """(ddt, member) pairs selected in at least one usage across all tops."""
    included: set[tuple[str, str]] = set()

    def walk(top: str, ddt: str, prefix: str, stack: list[str]) -> None:
        for m in types[ddt].members:
            rel = f"{prefix}.{m.name}" if prefix else m.name
            if m.type_name in types:
                if m.type_name in stack:
                    continue
                walk(top, m.type_name, rel, stack + [m.type_name])
            elif leaf_kind(m.type_name) is not None:
                if f"{top}|{rel}" not in excluded:
                    included.add((ddt, m.name))

    for top in top_ddts:
        walk(top, top, "", [top])
    return included


def build_udt_specs(
    types: dict[str, DdtType], top_ddts: list[str], excluded: set[str],
) -> tuple[dict[str, UdtSpec], list[str], list[str]]:
    """Return ({ddt: spec}, dependency-first order, warnings). UDTs whose
    every member is excluded/unsupported are dropped."""
    included = _collect_included(types, top_ddts, excluded)
    specs: dict[str, UdtSpec] = {}
    order: list[str] = []
    warnings: list[str] = []

    udt_scope = NameScope(warnings, "DDT")

    def build(ddt: str, stack: list[str]) -> UdtSpec | None:
        if ddt in specs:
            return specs[ddt]
        spec = UdtSpec(ddt=ddt)
        fields = NameScope(warnings, f"{ddt} member")
        for m in types[ddt].members:
            if m.type_name in types:
                if m.type_name in stack:
                    warnings.append(f"{ddt}.{m.name}: recursive DDT (skipped)")
                    continue
                if build(m.type_name, stack + [m.type_name]) is not None:
                    spec.structs.append(
                        (m.name, fields.add(m.name), m.type_name))
            elif (ddt, m.name) in included:
                vtype = VIJEO_TYPE.get(m.type_name.strip().upper())
                if vtype is None:
                    warnings.append(f"{ddt}.{m.name}: type '{m.type_name}' has "
                                    "no Vijeo equivalent (skipped)")
                    continue
                if vtype == "UINT":
                    # Vijeo's UDT import rejects UINT fields (the whole
                    # structure fails, which halts the variables import at
                    # that type's first instance). INT is the same 16 bits
                    # on the wire.
                    warnings.append(
                        f"{ddt}.{m.name}: UINT not supported in Vijeo UDT "
                        "fields - imported as INT (values > 32767 display "
                        "negative)")
                    vtype = "INT"
                spec.elementary.append((m.name, fields.add(m.name), m.comment, vtype))
        if not spec.elementary and not spec.structs:
            return None
        # room for the '_UDT' suffix within Vijeo's 32-char limit; the base
        # is also the folder-name stem
        spec.base = udt_scope.add(ddt, max_len=VIJEO_MAX_NAME - 4)
        specs[ddt] = spec
        order.append(ddt)
        return spec

    for top in top_ddts:
        if top in types:
            build(top, [top])
    # user-type IDs start at 101: every real Vijeo export observed begins at
    # 101+ (101/107/111) — 100 appears to be reserved and crashes the import
    for i, ddt in enumerate(order):
        specs[ddt].type_id = 101 + i
    return specs, order, warnings


def generate_udt_file(specs: dict[str, UdtSpec], order: list[str],
                      types: dict[str, DdtType]) -> str:
    lines = ["<typeList>"]
    for ddt in order:
        spec = specs[ddt]
        lines.append(f'  <structure name="{escape(spec.udt_name)}" '
                     f'isAnonymous="false" typeID="{spec.type_id}">')
        lines.append("    <comment></comment>")
        struct_types = {ce: (vj, child) for ce, vj, child in spec.structs}
        elem = {ce: (vj, comment, vtype)
                for ce, vj, comment, vtype in spec.elementary}
        for m in types[ddt].members:  # preserve CE member order
            if m.name in struct_types:
                vj_name, child = struct_types[m.name]
                ftype, comment = str(specs[child].type_id), m.comment
            elif m.name in elem:
                vj_name, comment, ftype = elem[m.name]
            else:
                continue
            lines.append(f'    <field name="{escape(vj_name)}" type="{escape(ftype)}">')
            lines.append(f"      <comment>{escape(comment or '')}</comment>")
            lines.append("      <configuredProperties/>")
            lines.append("    </field>")
        lines.append("  </structure>")
    lines.append("</typeList>")
    lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------ CSV rows

def _external_row(kind: str, name: str, vtype: str, desc: str,
                  scan_group: str, address: str) -> str:
    cells = {
        "Type": kind, "Name": name, "Data Type": vtype,
        "Data Source": "External", "Dimension": "0",
        "Description": _desc(desc), "Data Sharing": "None",
        "Alarm": "Disable", "Language1 ID": "1", "Scan Group": scan_group,
        "Device Address": _quote(address),
        "IndirectEnabled": "Disable",
        "LogUserOperationsOnVariable": "Disable",
    }
    fmt = _INT_FORMAT.get(vtype)
    if fmt:
        cells["Data Format"], cells["Signed"], cells["Data Length"] = fmt
    if vtype != "BOOL":
        cells["InputRange"] = "Disable"
        cells["DataScaling"] = "Disable"
    return _row(**cells)


def _ddt_variable_row(name: str, udt_name: str, desc: str, scan_group: str) -> str:
    return _row(**{
        "Type": "DDTVariable", "Name": name, "Data Type": udt_name,
        "Data Source": "External", "Dimension": "0",
        "Description": _desc(desc), "Data Sharing": "None",
        "Language1 ID": "1", "Scan Group": scan_group,
        "Device Address": '""',
    })


def _popup_name(base: str) -> str:
    return safe_vijeo_name(f"{base}_Popup")


def _popup_row(folder: str, base: str) -> str:
    return _row(**{
        "Type": "Variable", "Name": f"{folder}.{_popup_name(base)}",
        "Data Type": "STRING", "Data Source": "Internal", "Dimension": "0",
        "Description": '""', "Initial Value": '""',
        "NumofBytes": str(POPUP_BYTES), "Data Sharing": "None",
        "Language1 ID": "1", "Retentive": "Disable",
        "LogUserOperationsOnVariable": "Disable",
    })


def _reference_row(folder: str, base: str, member: str, vtype: str) -> str:
    return _row(**{
        "Type": "Variable", "Name": f"{folder}.{member}", "Data Type": vtype,
        "Data Source": "InternalReference", "Dimension": "0",
        "Description": '""', "Language1 ID": "1",
        "Device Address": _quote(
            f"%s.{member}({folder}.{_popup_name(base)}  )"),
    })


# ---------------------------------------------------------------- entrypoint

def generate_vijeo_files(
    types: dict[str, DdtType],
    tags: list[Tag],
    plan: MirrorPlan,
    state: SidecarState,
    scan_group: str,
) -> tuple[str, str, list[str]]:
    """Return (udt_file_text, variables_csv_text, warnings)."""
    top_ddts = [t for t in state.selected_types if t in types]
    excluded = set(state.deselected_type_members)
    specs, order, warnings = build_udt_specs(types, top_ddts, excluded)
    udt_text = generate_udt_file(specs, order, types)

    addresses = {a.leaf.full_path: a.address for a in plan.assignments}

    # name every exported variable FIRST: folder names must then be chosen
    # so that NO variable name starts with a folder name — Vijeo's import
    # halts on any variable sharing a folder's name as a prefix (proven by
    # probes: folder 'FCV' + variable 'FCV_9x' fails, either alone is fine).
    tag_scope = NameScope(warnings, "tag")
    ddt_instances: list[tuple[Tag, str]] = []       # (tag, vijeo name)
    for tag in tags:
        if tag.type_name in specs:
            ddt_instances.append((tag, tag_scope.add(tag.name)))
    standalone: list[tuple[object, str]] = []       # (assignment, vijeo name)
    for a in plan.assignments:
        if not a.leaf.ddt_type:
            ce = a.leaf.type_name.strip().upper()
            if ce not in VIJEO_TYPE:
                warnings.append(f"{a.leaf.full_path}: type '{a.leaf.type_name}'"
                                " has no Vijeo equivalent (skipped)")
                continue
            standalone.append((a, tag_scope.add(a.leaf.full_path)))
    all_var_names = ([n for _t, n in ddt_instances]
                     + [n for _a, n in standalone])

    # folder tree: one folder per UDT, nested under the first-using parent
    folder_segment: dict[str, str] = {}
    folders: dict[str, str] = {}
    folder_order: list[str] = []

    def segment_for(ddt: str) -> str:
        seg = safe_vijeo_name(f"{specs[ddt].base}_Grp")
        while any(v == seg or v.startswith(seg) for v in all_var_names):
            seg = safe_vijeo_name(seg + "X")
        return seg

    def place(ddt: str, parent_path: str | None) -> None:
        if ddt not in specs or ddt in folders:
            return
        seg = folder_segment.setdefault(ddt, segment_for(ddt))
        folders[ddt] = seg if parent_path is None else f"{parent_path}.{seg}"
        folder_order.append(ddt)
        for _ce, _vj, child in specs[ddt].structs:
            place(child, folders[ddt])

    for top in top_ddts:
        place(top, None)

    rows = [CSV_BANNER, ",".join(CSV_COLUMNS)]
    rows.extend(f'Folder,{folders[d]},,,,"",' for d in folder_order)

    def emit_subvars(instance: str, vj_instance: str, ddt: str,
                     ce_prefix: str, vj_prefix: str) -> None:
        spec = specs[ddt]
        struct_types = {ce: (vj, child) for ce, vj, child in spec.structs}
        elem = {ce: (vj, comment, vtype)
                for ce, vj, comment, vtype in spec.elementary}
        for m in types[ddt].members:
            ce_rel = f"{ce_prefix}.{m.name}" if ce_prefix else m.name
            full = f"{instance}.{ce_rel}"
            if m.name in struct_types:
                vj_member, child = struct_types[m.name]
                vj_rel = f"{vj_prefix}.{vj_member}" if vj_prefix else vj_member
                emit_subvars(instance, vj_instance, child, ce_rel, vj_rel)
            elif m.name in elem:
                vj_member, comment, vtype = elem[m.name]
                vj_rel = f"{vj_prefix}.{vj_member}" if vj_prefix else vj_member
                addr = addresses.get(full, "")
                if not addr:
                    warnings.append(f"{full}: no mirror address (deselected "
                                    "per-variable) - imported unaddressed")
                # Data Type must match the UDT field type exactly, or the
                # element rows are rejected on import
                rows.append(_external_row(
                    "SubVariable", f"{vj_instance}.{vj_rel}", vtype,
                    comment, scan_group, addr))

    for tag, vj_instance in ddt_instances:
        rows.append(_ddt_variable_row(
            vj_instance, specs[tag.type_name].udt_name, tag.comment,
            scan_group))
        emit_subvars(tag.name, vj_instance, tag.type_name, "", "")

    # standalone elementary tags keep their CE names (sanitized if needed)
    for a, vj_name in standalone:
        rows.append(_external_row(
            "Variable", vj_name, VIJEO_TYPE[a.leaf.type_name.strip().upper()],
            a.leaf.comment, scan_group, a.address))

    # per-UDT folder: popup STRING + InternalReference per elementary member
    for ddt in folder_order:
        spec, folder = specs[ddt], folders[ddt]
        rows.append(_popup_row(folder, spec.base))
        for _ce, vj_name, _comment, vtype in spec.elementary:
            if vtype == "UDINT":
                # internal/reference variables cannot be UDINT in Vijeo;
                # DINT is the same 32 bits (external SubVariables stay UDINT)
                warnings.append(
                    f"{folder}.{vj_name}: reference variable created as DINT "
                    "(Vijeo internal tags cannot be UDINT)")
                vtype = "DINT"
            rows.append(_reference_row(folder, spec.base, vj_name, vtype))

    rows.append("")
    return udt_text, "\n".join(rows), warnings
