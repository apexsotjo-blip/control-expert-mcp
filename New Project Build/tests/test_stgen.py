from ddt_mirror.core.model import Access, Assignment, FlatLeaf, LeafKind
from ddt_mirror.core.stgen import CE_MAX_IDENT, generate_st, mirror_var_name


def _assign(path, kind, access, address, mirror="", premapped=False):
    inst, _, rel = path.partition(".")
    leaf = FlatLeaf(instance=inst, rel_path=rel, type_name="X", kind=kind,
                    access=access, ddt_type="T" if rel else "")
    return Assignment(leaf=leaf, address=address, mirror_var=mirror,
                      premapped=premapped)


def test_generated_st_structure_and_order():
    st = generate_st(
        [
            _assign("Pump1.Flow_PV", LeafKind.WORD2, Access.READ, "%MW1000",
                    "HMI_Pump1_Flow_PV"),
            _assign("Pump1.Cmd", LeafKind.BIT, Access.READ_WRITE, "%M100"),
            _assign("Pump1.Ctrl.Man_SP", LeafKind.WORD2, Access.READ_WRITE,
                    "%MW1002", "HMI_Pump1_Ctrl_Man_SP"),
            _assign("Line_Speed", LeafKind.WORD1, Access.READ, "%MW1004"),
            _assign("Located_Speed", LeafKind.WORD1, Access.READ, "%MW50",
                    premapped=True),
        ],
        project="Demo", timestamp="2026-07-14T00:00:00",
    )
    # Read/Write copies (tag := address) precede Read copies (address := tag)
    assert st.index("Pump1.Cmd := %M100;") < st.index("%MW1004 := Line_Speed;")
    # WORD2 goes through the mirror variable, not the %MW literal
    assert "HMI_Pump1_Flow_PV := Pump1.Flow_PV;" in st
    assert "Pump1.Ctrl.Man_SP := HMI_Pump1_Ctrl_Man_SP;" in st
    assert "%MW1000 := Pump1.Flow_PV" not in st
    # premapped tags get no copy at all
    assert "Located_Speed" not in st
    assert "DO NOT EDIT" in st


def test_generated_st_is_deterministic():
    args = ([_assign("A.X", LeafKind.WORD1, Access.READ, "%MW1000")],)
    assert (generate_st(*args, timestamp="t") == generate_st(*args, timestamp="t"))


def test_mirror_var_name_limits():
    assert mirror_var_name("Pump1.Flow_PV") == "HMI_Pump1_Flow_PV"
    long = mirror_var_name("VeryLongInstanceName.Deep.Nested.Structure.Member_SP")
    assert len(long) <= CE_MAX_IDENT
    assert long.startswith("HMI_")
    # stable across calls
    assert long == mirror_var_name("VeryLongInstanceName.Deep.Nested.Structure.Member_SP")


def test_mirror_var_name_never_has_consecutive_underscores():
    # regression: 'HMI_UF1_Ka_Flush_StepTimer__047F' -> E1219 (truncation
    # seam landed on an underscore before the hash suffix)
    name = mirror_var_name("UF1_Ka.Flush_StepTimer_Preset")
    assert len(name) <= CE_MAX_IDENT
    assert "__" not in name
    assert not name.endswith("_")
    # doubles in the source path are collapsed too
    weird = mirror_var_name("Tank__2.Level___SP")
    assert "__" not in weird and weird == "HMI_Tank_2_Level_SP"
