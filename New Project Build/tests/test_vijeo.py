import csv
import io

from lxml import etree

from ddt_mirror.codegen.vijeo import generate_vijeo_files
from ddt_mirror.core.engine import ProjectData, build_plan
from ddt_mirror.core.flatten import flatten_tags
from ddt_mirror.core.model import DdtMember, DdtType, Tag
from ddt_mirror.core.persist import SidecarState

# mirrors the HMI_SAMPLES pattern: Pump DDT with nested Status DDT
TYPES = {
    "Status": DdtType("Status", [
        DdtMember("good", "BOOL"),
        DdtMember("bad", "DINT"),
        DdtMember("testss", "REAL"),
    ]),
    "Pump": DdtType("Pump", [
        DdtMember("Start", "BOOL", "start cmd"),
        # comma + quotes: Vijeo's CSV import splits naively on commas, so
        # descriptions must be neutralized or the whole DDT block is rejected
        DdtMember("FeedBack", "REAL", 'flow, m3/h, "raw" value'),
        DdtMember("status", "Status"),
    ]),
}
TAGS = [Tag("Pump1", "Pump", "Feed pump"), Tag("Pump2", "Pump"),
        Tag("Line_Speed", "INT")]


def _generate(state=None):
    state = state or SidecarState()
    if not state.selected_types:
        state.selected_types = ["Pump", "INT"]
    leaves, warnings = flatten_tags(TAGS, TYPES)
    data = ProjectData(types=TYPES, tags=TAGS, leaves=leaves, warnings=warnings)
    plan, _alloc = build_plan(data, state, timestamp="t0")
    return generate_vijeo_files(TYPES, TAGS, plan, state, "ModbusEquipment01")


def _rows(csv_text):
    lines = csv_text.splitlines()
    assert lines[0] == "'5.1.0, Vijeo-Designer 6.2.11 CSV output"
    reader = csv.reader(io.StringIO("\n".join(lines[1:])))
    header = next(reader)
    out = []
    for raw in reader:
        row = dict(zip(header, raw + [""] * (len(header) - len(raw))))
        out.append(row)
    return out


def test_udt_file_structure_and_nesting():
    udt_text, _, _ = _generate()
    root = etree.fromstring(udt_text.encode("utf-8"))
    assert root.tag == "typeList"
    structs = {s.get("name"): s for s in root.findall("structure")}
    assert set(structs) == {"Pump_UDT", "Status_UDT"}
    # dependency (nested type) emitted first, sample-style
    assert [s.get("name") for s in root.findall("structure")][0] == "Status_UDT"
    # nested member references the child's numeric typeID
    status_id = structs["Status_UDT"].get("typeID")
    pump_fields = {f.get("name"): f.get("type")
                   for f in structs["Pump_UDT"].findall("field")}
    assert pump_fields == {"Start": "BOOL", "FeedBack": "REAL",
                           "status": status_id}
    assert all(s.get("isAnonymous") == "false" for s in structs.values())


def test_csv_instances_folders_and_addresses():
    _, csv_text, warnings = _generate()
    rows = _rows(csv_text)
    by_type = {}
    for r in rows:
        by_type.setdefault(r["Type"], []).append(r)

    # one folder per UDT, nested path for the nested UDT; folder names carry
    # the _Grp suffix so no variable name can start with a folder name
    # (Vijeo halts on folder-name-prefixed variables)
    assert [r["Name"] for r in by_type["Folder"]] == [
        "Pump_Grp", "Pump_Grp.Status_Grp"]

    # instances keep CE names; sub-variables carry allocated addresses
    ddt_vars = {r["Name"]: r for r in by_type["DDTVariable"]}
    assert ddt_vars["Pump1"]["Data Type"] == "Pump_UDT"
    assert ddt_vars["Pump1"]["Scan Group"] == "ModbusEquipment01"
    subs = {r["Name"]: r for r in by_type["SubVariable"]}
    assert subs["Pump1.Start"]["Device Address"].startswith("%M")
    assert subs["Pump1.status.bad"]["Data Format"] == "BIN"
    assert subs["Pump1.status.bad"]["Signed"] == "2sComplement"
    assert subs["Pump1.status.bad"]["Data Length"] == "32Bits"
    assert subs["Pump1.FeedBack"]["Device Address"].startswith("%MW")
    assert subs["Pump1.FeedBack"]["Data Format"] == ""  # REALs carry no format
    # every 32-bit address is even (CE alignment) and unique
    addrs = [r["Device Address"] for r in by_type["SubVariable"]]
    assert len(addrs) == len(set(addrs)) == 10
    assert not warnings

    # standalone tag exported as a plain external Variable with its CE name
    plain = {r["Name"]: r for r in by_type["Variable"]}
    assert plain["Line_Speed"]["Data Source"] == "External"
    assert plain["Line_Speed"]["Device Address"].startswith("%MW")


def test_no_variable_name_shares_a_folder_prefix():
    """Regression (probe G): folder 'FCV' + variable 'FCV_9x' halts the
    import. No exported variable name may start with a folder name unless it
    is properly folder-scoped ('folder.' + member)."""
    _, csv_text, _ = _generate()
    rows = _rows(csv_text)
    folder_paths = [r["Name"] for r in rows if r["Type"] == "Folder"]
    for r in rows:
        if r["Type"] == "Folder":
            continue
        for folder in folder_paths:
            if r["Name"].startswith(folder):
                assert r["Name"].startswith(folder + "."), (
                    f"variable '{r['Name']}' shares folder prefix '{folder}'")

    # the guard extends the folder name when a tag would collide with it
    types = {"FCV": DdtType("FCV", [DdtMember("Remote", "BOOL")])}
    tags = [Tag("FCV_1", "FCV"), Tag("FCV_GrpSpare", "BOOL")]
    leaves, _ = flatten_tags(tags, types)
    data = ProjectData(types=types, tags=tags, leaves=leaves)
    state = SidecarState()
    state.selected_types = ["FCV", "BOOL"]
    plan, _alloc = build_plan(data, state, timestamp="t0")
    _, csv2, _ = generate_vijeo_files(types, tags, plan, state, "SG1")
    rows2 = _rows(csv2)
    folder = next(r["Name"] for r in rows2 if r["Type"] == "Folder")
    assert folder != "FCV_Grp"  # extended to dodge FCV_GrpSpare
    assert not any(r["Name"].startswith(folder) and r["Type"] != "Folder"
                   and not r["Name"].startswith(folder + ".") for r in rows2)


def test_descriptions_never_contain_commas_or_quotes():
    """Regression: 4 rows with commas inside quoted descriptions (FCV
    comments like 'AO or DO, 1=DO, 0=AO') halted the whole import — Vijeo's
    CSV parser splits on commas regardless of quoting."""
    _, csv_text, _ = _generate()
    lines = csv_text.splitlines()
    header_cols = lines[1].count(",")
    for line in lines[2:]:
        if not line:
            continue
        # naive comma split must line up with the real column layout
        assert line.count(",") <= header_cols + 1, line
        for cell in next(iter(csv.reader(io.StringIO(line)))):
            assert "," not in cell, line
    fb = next(r for r in _rows(csv_text) if r["Name"] == "Pump1.FeedBack")
    assert fb["Description"] == "flow; m3/h; 'raw' value"


def test_csv_popup_and_reference_variables():
    _, csv_text, _ = _generate()
    rows = {r["Name"]: r for r in _rows(csv_text) if r["Type"] == "Variable"}

    pump_popup = rows["Pump_Grp.Pump_Popup"]
    assert pump_popup["Data Type"] == "STRING"
    assert pump_popup["Data Source"] == "Internal"
    status_popup = rows["Pump_Grp.Status_Grp.Status_Popup"]
    assert status_popup["Data Source"] == "Internal"  # each folder has one

    start_ref = rows["Pump_Grp.Start"]
    assert start_ref["Data Source"] == "InternalReference"
    assert start_ref["Device Address"] == "%s.Start(Pump_Grp.Pump_Popup  )"
    good_ref = rows["Pump_Grp.Status_Grp.good"]
    assert good_ref["Device Address"] == (
        "%s.good(Pump_Grp.Status_Grp.Status_Popup  )")


def test_leading_underscore_names_sanitized():
    """Regression: CE DDT '_AI' produced UDT '_AI_UDT' whose leading
    underscore crashes Vijeo's UDT import."""
    types = {"_AI": DdtType("_AI", [
        DdtMember("HH_Alarm", "BOOL"),
        DdtMember("_Raw", "INT"),
    ])}
    tags = [Tag("_FT_101", "_AI")]
    leaves, _ = flatten_tags(tags, types)
    data = ProjectData(types=types, tags=tags, leaves=leaves)
    state = SidecarState()
    state.selected_types = ["_AI"]
    plan, _alloc = build_plan(data, state, timestamp="t0")
    udt_text, csv_text, warnings = generate_vijeo_files(
        types, tags, plan, state, "SG1")

    root = etree.fromstring(udt_text.encode("utf-8"))
    struct = root.find("structure")
    assert struct.get("name") == "AI_UDT"  # letter-first
    assert [f.get("name") for f in struct.findall("field")] == ["HH_Alarm", "Raw"]

    rows = _rows(csv_text)
    names = [r["Name"] for r in rows]
    assert all(not seg.startswith("_")
               for n in names for seg in n.split("."))
    ddt_row = next(r for r in rows if r["Type"] == "DDTVariable")
    assert ddt_row["Name"] == "FT_101" and ddt_row["Data Type"] == "AI_UDT"
    sub = next(r for r in rows if r["Type"] == "SubVariable"
               and r["Name"].endswith("Raw"))
    assert sub["Name"] == "FT_101.Raw"
    assert any("renamed" in w for w in warnings)


def test_digit_before_dot_kept_as_is():
    """Digits before a '.' are fine in Vijeo (the earlier suffix workaround
    was removed): instance UF1 stays UF1, leaf digits stay too."""
    types = {
        "Step2": DdtType("Step2", [DdtMember("running", "BOOL")]),
        "UF": DdtType("UF", [
            DdtMember("Start", "BOOL"),
            DdtMember("Count1", "INT"),
            DdtMember("Flush2", "Step2"),
        ]),
    }
    tags = [Tag("UF1", "UF"), Tag("Line2_Speed3", "INT")]
    leaves, _ = flatten_tags(tags, types)
    data = ProjectData(types=types, tags=tags, leaves=leaves)
    state = SidecarState()
    state.selected_types = ["UF", "INT"]
    plan, _alloc = build_plan(data, state, timestamp="t0")

    _udt, csv_text, _ = generate_vijeo_files(types, tags, plan, state, "SG1")
    names = {r["Name"] for r in _rows(csv_text)}
    assert "UF1" in names                        # instance keeps its digit
    assert "UF1.Start" in names
    assert "UF1.Count1" in names
    assert "UF1.Flush2.running" in names         # struct member keeps digit
    assert "Line2_Speed3" in names


def test_uint_udt_fields_become_int():
    """Regression: Vijeo's UDT import rejects UINT fields (FCV_UDT with
    PosScaledErr:WORD was the only failing structure — 'Incorrect usage of
    variable element' halted the CSV at FCV_1.Remote). UDT fields and their
    SubVariable rows both use INT; standalone UINT variables stay UINT."""
    types = {"FCV": DdtType("FCV", [
        DdtMember("Remote", "BOOL"),
        DdtMember("PosScaledErr", "WORD"),
        DdtMember("Timer", "TIME"),
    ])}
    tags = [Tag("FCV_1", "FCV"), Tag("Spare_Word", "WORD")]
    leaves, _ = flatten_tags(tags, types)
    data = ProjectData(types=types, tags=tags, leaves=leaves)
    state = SidecarState()
    state.selected_types = ["FCV", "WORD"]
    plan, _alloc = build_plan(data, state, timestamp="t0")
    udt_text, csv_text, warnings = generate_vijeo_files(
        types, tags, plan, state, "SG1")

    root = etree.fromstring(udt_text.encode("utf-8"))
    fields = {f.get("name"): f.get("type")
              for f in root.find("structure").findall("field")}
    assert fields["PosScaledErr"] == "INT"   # not UINT
    assert fields["Timer"] == "UDINT"        # UDINT proven fine, unchanged
    assert "UINT" not in udt_text

    rows = _rows(csv_text)
    sub = next(r for r in rows if r["Name"] == "FCV_1.PosScaledErr")
    assert sub["Data Type"] == "INT"         # matches the UDT field
    assert (sub["Data Format"], sub["Signed"], sub["Data Length"]) == (
        "BIN", "2sComplement", "16Bits")
    standalone = next(r for r in rows if r["Name"] == "Spare_Word")
    assert standalone["Data Type"] == "UINT"  # plain variables keep UINT
    assert any("UINT not supported" in w for w in warnings)

    # internal reference variables cannot be UDINT: folder ref becomes DINT
    # while the external SubVariable keeps UDINT
    timer_sub = next(r for r in rows if r["Name"] == "FCV_1.Timer")
    assert timer_sub["Data Type"] == "UDINT"
    timer_ref = next(r for r in rows
                     if r["Name"] == "FCV_Grp.Timer"
                     and r["Data Source"] == "InternalReference")
    assert timer_ref["Data Type"] == "DINT"
    assert any("cannot be UDINT" in w for w in warnings)


def test_type_level_exclusion_drops_udt_field():
    state = SidecarState()
    state.selected_types = ["Pump", "INT"]
    state.deselected_type_members = ["Pump|status.testss"]
    udt_text, csv_text, _ = _generate(state)
    root = etree.fromstring(udt_text.encode("utf-8"))
    status = next(s for s in root.findall("structure")
                  if s.get("name") == "Status_UDT")
    assert [f.get("name") for f in status.findall("field")] == ["good", "bad"]
    assert "testss" not in csv_text
