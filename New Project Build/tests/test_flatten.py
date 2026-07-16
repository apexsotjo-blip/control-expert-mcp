from ddt_mirror.core.flatten import flatten_tags
from ddt_mirror.core.model import DdtMember, DdtType, LeafKind, Tag


def test_flatten_expands_nested_ddts(parsed):
    types, tags = parsed
    leaves, warnings = flatten_tags(tags, types)
    by_path = {l.full_path: l for l in leaves}

    assert by_path["Pump1.Cmd"].kind is LeafKind.BIT
    assert by_path["Pump1.Flow_PV"].kind is LeafKind.WORD2
    assert by_path["Pump1.Ctrl.Man_SP"].kind is LeafKind.WORD2
    assert by_path["Pump1.Ctrl.Mode"].kind is LeafKind.WORD1
    assert by_path["Pump1.Ctrl.Man_SP"].ddt_type == "PUMP_T"  # override key is instance type
    assert by_path["Pump2.Cmd"].kind is LeafKind.BIT

    # standalone tags
    assert by_path["Line_Speed"].kind is LeafKind.WORD1
    assert by_path["Line_Speed"].ddt_type == ""
    assert by_path["E_Stop"].kind is LeafKind.BIT
    assert by_path["Tank_Level"].kind is LeafKind.WORD2
    assert by_path["Located_Speed"].located == "%MW50"

    # arrays + strings warned, DFBs silently skipped
    assert any("Pump1.Log" in w for w in warnings)
    assert any("Recipe_Name" in w for w in warnings)
    assert "Mixer" not in by_path


def test_cycle_guard():
    types = {
        "A_T": DdtType("A_T", [DdtMember("x", "INT"), DdtMember("child", "B_T")]),
        "B_T": DdtType("B_T", [DdtMember("back", "A_T"), DdtMember("y", "BOOL")]),
    }
    leaves, warnings = flatten_tags([Tag("Root", "A_T")], types)
    paths = {l.full_path for l in leaves}
    assert "Root.x" in paths and "Root.child.y" in paths
    assert any("recursive" in w for w in warnings)
