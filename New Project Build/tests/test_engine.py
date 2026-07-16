import csv
import io

from ddt_mirror.core.engine import build_plan, scan_project, select_leaves, type_summary
from ddt_mirror.core.flatten import flatten_tags
from ddt_mirror.core.engine import ProjectData
from ddt_mirror.core.persist import SidecarState


def _data(parsed) -> ProjectData:
    types, tags = parsed
    leaves, warnings = flatten_tags(tags, types)
    return ProjectData(types=types, tags=tags, leaves=leaves, warnings=warnings)


def _state(**kw) -> SidecarState:
    state = SidecarState()
    state.selected_types = kw.get("selected_types",
                                  ["PUMP_T", "INT", "BOOL", "REAL"])
    state.deselected_leaves = kw.get("deselected_leaves", [])
    state.access_overrides = kw.get("access_overrides", {})
    return state


def test_type_summary_counts(parsed):
    rows = {r["type"]: r for r in type_summary(_data(parsed))}
    assert rows["PUMP_T"]["count"] == 2 and rows["PUMP_T"]["kind"] == "DDT"
    assert rows["INT"]["count"] == 2   # Line_Speed + Located_Speed
    assert rows["REAL"]["count"] == 1  # Tank_Level


def test_select_leaves_honours_types_and_deselection(parsed):
    data = _data(parsed)
    state = _state(deselected_leaves=["Pump2.Ctrl.Mode"])
    picked = {l.full_path for l in select_leaves(data, state)}
    assert "Pump1.Cmd" in picked
    assert "Pump2.Ctrl.Mode" not in picked      # user unchecked
    assert "Pump1.Ctrl.Mode" in picked
    state2 = _state(selected_types=["INT"])
    picked2 = {l.full_path for l in select_leaves(data, state2)}
    assert picked2 == {"Line_Speed", "Located_Speed"}


def test_build_plan_end_to_end(parsed):
    data = _data(parsed)
    state = _state()
    plan, alloc = build_plan(data, state, project_name="Fixture", timestamp="t0")

    # only WORD2 leaves need created variables
    for spec in plan.new_variables:
        assert spec["type_name"] == "REAL"
        assert spec["address"].startswith("%MW")
    names = {v["name"] for v in plan.new_variables}
    assert "HMI_Pump1_Flow_PV" in names and "HMI_Pump2_Ctrl_Man_SP" in names

    # premapped tag kept out of ST but present in CSV
    assert "Located_Speed" not in plan.st_source
    rows = list(csv.DictReader(io.StringIO(plan.csv_text)))
    by_path = {r["PlcPath"]: r for r in rows}
    assert by_path["Located_Speed"]["Address"] == "%MW50"
    assert by_path["Located_Speed"]["Premapped"] == "yes"
    assert by_path["Pump1.Flow_PV"]["Registers"] == "2"
    assert by_path["Pump1.Cmd"]["Access"] == "R/W"

    # caller's state untouched until apply_plan persists the new alloc
    assert state.alloc.leaves == {}
    assert alloc.leaves


def test_type_level_exclusion_applies_to_future_instances(parsed):
    data = _data(parsed)
    state = _state()
    state.deselected_type_members = ["PUMP_T|Ctrl.Mode"]
    picked = {l.full_path for l in select_leaves(data, state)}
    # excluded for every existing instance without naming them individually —
    # a Pump3 added later would be excluded the same way
    assert "Pump1.Ctrl.Mode" not in picked
    assert "Pump2.Ctrl.Mode" not in picked
    assert "Pump1.Ctrl.Man_SP" in picked


def test_word_and_uint_get_typed_mirror_vars():
    """%MWi literals are INT-typed in ST; WORD/UINT need exact-type located
    variables or CE raises E1092 (seen on FCV_x.PosScaledErr:WORD)."""
    from ddt_mirror.core.model import DdtMember, DdtType, Tag

    types = {"FCV_T": DdtType("FCV_T", [
        DdtMember("PosScaledErr", "WORD"),
        DdtMember("Mode", "INT"),
        DdtMember("Cnt", "UINT"),
    ])}
    tags = [Tag("FCV_1", "FCV_T")]
    leaves, _ = flatten_tags(tags, types)
    data = ProjectData(types=types, tags=tags, leaves=leaves)
    state = _state(selected_types=["FCV_T"])
    plan, _ = build_plan(data, state, timestamp="t0")

    by_name = {v["name"]: v for v in plan.new_variables}
    assert by_name["HMI_FCV_1_PosScaledErr"]["type_name"] == "WORD"
    assert by_name["HMI_FCV_1_Cnt"]["type_name"] == "UINT"
    assert "HMI_FCV_1_Mode" not in by_name  # INT keeps the direct literal
    # ST references the typed variables, never a raw %MW for WORD/UINT
    assert "HMI_FCV_1_PosScaledErr := FCV_1.PosScaledErr;" in plan.st_source
    assert ":= FCV_1.Mode" in plan.st_source and "%MW" in plan.st_source


def test_mirror_name_collision_gets_distinct_names():
    """Distinct paths that sanitize to the same identifier must yield
    distinct mirror variable names (the '#' fallback used to be a no-op)."""
    from ddt_mirror.core.model import DdtMember, DdtType, Tag

    types = {
        "A_T": DdtType("A_T", [DdtMember("B", "REAL")]),
        "AB_T": DdtType("AB_T", [DdtMember("PV", "REAL")]),
    }
    # 'P1_A.B' and 'P1.A_B' both sanitize to HMI_P1_A_B
    tags = [Tag("P1_A", "A_T"), Tag("P1", "AB_T")]
    types["AB_T"].members[0] = DdtMember("A_B", "REAL")
    leaves, _ = flatten_tags(tags, types)
    data = ProjectData(types=types, tags=tags, leaves=leaves)
    state = _state(selected_types=["A_T", "AB_T"])
    plan, _ = build_plan(data, state, timestamp="t0")

    names = [v["name"] for v in plan.new_variables]
    assert len(names) == 2
    assert len(set(names)) == 2, f"duplicate mirror names: {names}"
    assert any("collision" in w for w in plan.warnings)
    # deterministic across runs
    plan2, _ = build_plan(data, state, timestamp="t0")
    assert [v["name"] for v in plan2.new_variables] == names


def test_build_plan_stable_across_runs(parsed):
    data = _data(parsed)
    state = _state()
    plan1, alloc1 = build_plan(data, state, timestamp="t0")
    state.alloc = alloc1
    plan2, _ = build_plan(data, state, timestamp="t0")
    assert plan1.st_source == plan2.st_source
    assert plan1.csv_text == plan2.csv_text
