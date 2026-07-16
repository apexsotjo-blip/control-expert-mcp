from ddt_mirror.core.model import Access, FlatLeaf, leaf_kind
from ddt_mirror.core.rtu import (
    DT_ANALOG, DT_COUNTER, DT_DIGITAL, GROUP_AI, GROUP_AO, GROUP_BI,
    GROUP_COUNTER, LVT_INT, LVT_REAL, LVT_UDINT, RtuAllocState, RtuEntry,
    allocate_rtu, generate_rtu_st, group_space, object_name, rtu_spec,
)


def _leaf(path: str, type_name: str, access=Access.READ) -> FlatLeaf:
    instance, _, rel = path.partition(".")
    return FlatLeaf(instance=instance, rel_path=rel, type_name=type_name,
                    kind=leaf_kind(type_name), ddt_type="T", access=access)


# ------------------------------------------------------------------- specs

def test_spec_mapping_matches_reference_export():
    spec, _ = rtu_spec(_leaf("P.Run", "BOOL"))
    assert (spec.data_type, spec.group) == (DT_DIGITAL, GROUP_BI)
    spec, _ = rtu_spec(_leaf("P.PV", "REAL"))
    assert (spec.data_type, spec.logic_type, spec.group) == (
        DT_ANALOG, LVT_REAL, GROUP_AI)
    spec, _ = rtu_spec(_leaf("P.SP", "REAL", Access.READ_WRITE))
    assert spec.group == GROUP_AO
    spec, _ = rtu_spec(_leaf("P.Total", "UDINT"))
    assert (spec.data_type, spec.group) == (DT_COUNTER, GROUP_COUNTER)
    spec, cat = rtu_spec(_leaf("P.Total", "UDINT", Access.READ_WRITE))
    assert (spec.data_type, spec.logic_type) == (DT_ANALOG, LVT_UDINT)
    assert cat == "rw_udint"


def test_uint_word_become_int_with_conversions():
    spec, cat = rtu_spec(_leaf("P.Cnt", "UINT", Access.READ_WRITE))
    assert spec.logic_type == LVT_INT and cat == "uint_as_int"
    assert (spec.conv_to_tag, spec.conv_to_obj) == ("INT_TO_UINT", "UINT_TO_INT")
    spec, cat = rtu_spec(_leaf("P.Sts", "WORD"))
    assert spec.logic_type == LVT_INT and cat == "word_as_int"


def test_time_unsupported():
    spec, cat = rtu_spec(_leaf("P.Dur", "TIME"))
    assert spec is None and cat == "unsupported"


def test_group_space():
    assert group_space(GROUP_BI) == "g1"
    assert group_space(GROUP_AI) == "g30"
    assert group_space(GROUP_AO) == "g40"
    assert group_space(GROUP_COUNTER) == "g20"


def test_object_name_rules():
    assert object_name("Pump1.Ctrl.Man_SP") == "Pump1_Ctrl_Man_SP"
    long = object_name("VeryLongInstanceName.With.A.Deep.Member.Path_X")
    assert len(long) <= 32 and "__" not in long
    assert object_name("_5Weird.name")[0].isalpha()


# -------------------------------------------------------------- allocation

def _leaves():
    return [
        _leaf("Pump1.Cmd", "BOOL", Access.READ_WRITE),
        _leaf("Pump1.Running", "BOOL"),
        _leaf("Pump1.Flow_PV", "REAL"),
        _leaf("Pump1.Man_SP", "REAL", Access.READ_WRITE),
        _leaf("Pump1.Total", "UDINT"),
        _leaf("Line_Speed", "INT"),
    ]


def test_allocation_numbers_and_spaces():
    state = RtuAllocState()
    assignments, _ = allocate_rtu(state, _leaves(), set(), {}, set())
    by_path = {a.leaf.full_path: a.entry for a in assignments}
    # per-space DNP3 numbering starts at 1
    assert by_path["Pump1.Cmd"].dnp3_point == 1      # g1 space
    assert by_path["Pump1.Running"].dnp3_point == 2  # g1 space
    assert by_path["Pump1.Flow_PV"].dnp3_point == 1  # g30 space
    assert by_path["Line_Speed"].dnp3_point == 2     # g30 space
    assert by_path["Pump1.Man_SP"].dnp3_point == 1   # g40 space
    assert by_path["Pump1.Total"].dnp3_point == 1    # g20 space
    # registers: digitals in the coil range, analogs in 4xxxx
    assert by_path["Pump1.Cmd"].register == 1
    assert by_path["Pump1.Running"].register == 2
    assert by_path["Pump1.Flow_PV"].register == 40001
    assert by_path["Pump1.Man_SP"].register == 40002
    assert by_path["Pump1.Total"].register == 40003


def test_allocation_respects_workbook_usage():
    state = RtuAllocState()
    assignments, _ = allocate_rtu(
        state, _leaves(), set(),
        {"g1": {7}, "g30": {41}}, {3, 40010})
    by_path = {a.leaf.full_path: a.entry for a in assignments}
    assert by_path["Pump1.Cmd"].dnp3_point == 8
    assert by_path["Pump1.Flow_PV"].dnp3_point == 42
    assert by_path["Pump1.Cmd"].register == 4
    assert by_path["Pump1.Flow_PV"].register == 40011


def test_allocation_stable_across_runs():
    state = RtuAllocState()
    first, _ = allocate_rtu(state, _leaves(), set(), {}, set())
    snapshot = {a.leaf.full_path: (a.entry.name, a.entry.dnp3_point,
                                   a.entry.register) for a in first}
    # rerun with MORE workbook usage (someone added RTU points meanwhile)
    second, _ = allocate_rtu(state, _leaves(), set(),
                             {"g1": {50}}, {40020})
    for a in second:
        assert snapshot[a.leaf.full_path] == (
            a.entry.name, a.entry.dnp3_point, a.entry.register)


def test_deselected_tombstones_and_revives():
    state = RtuAllocState()
    allocate_rtu(state, _leaves(), set(), {}, set())
    point = state.entries["Pump1.Running"].dnp3_point
    remaining = [l for l in _leaves() if l.full_path != "Pump1.Running"]
    allocate_rtu(state, remaining, set(), {}, set())
    assert state.entries["Pump1.Running"].active is False
    revived, _ = allocate_rtu(state, _leaves(), set(), {}, set())
    by_path = {a.leaf.full_path: a.entry for a in revived}
    assert by_path["Pump1.Running"].dnp3_point == point
    assert by_path["Pump1.Running"].active is True


def test_access_change_retires_slot_and_renames():
    state = RtuAllocState()
    allocate_rtu(state, [_leaf("Pump1.Mode", "INT")], set(), {}, set())
    old = state.entries["Pump1.Mode"]
    changed, _ = allocate_rtu(
        state, [_leaf("Pump1.Mode", "INT", Access.READ_WRITE)],
        set(), {}, set())
    entry = changed[0].entry
    assert state.dead and state.dead[0]["name"] == old.name
    assert entry.name != old.name          # old object may still exist in RTU
    assert entry.group == GROUP_AO
    assert entry.dnp3_point == 1           # g40 space is fresh
    assert entry.register == old.register + 1  # old register never reused


def test_name_collision_with_workbook():
    state = RtuAllocState()
    assignments, _ = allocate_rtu(
        state, [_leaf("Pump1.Cmd", "BOOL")], {"pump1_cmd"}, {}, set())
    assert assignments[0].entry.name == "Pump1_Cmd_2"


def test_member_object_dodges_program_variable():
    """A member object may never take the name of a program variable, or
    that variable would silently bind to the object in the Logic Editor."""
    state = RtuAllocState()
    assignments, _ = allocate_rtu(
        state, [_leaf("Pump1.Cmd", "BOOL")], set(), {}, set(),
        program_names={"Pump1_Cmd", "Pump1"})
    assert assignments[0].entry.name == "Pump1_Cmd_2"


def test_standalone_tags_get_obj_suffix_and_value_copy():
    """T_SPx70_* variables are structs (.value member), so an object may
    never take a program variable's own name - standalone tags get a
    distinctly-named '<tag>_Obj' object plus a .value copy."""
    def standalone(name, type_name, access=Access.READ):
        return FlatLeaf(instance=name, rel_path="", type_name=type_name,
                        kind=leaf_kind(type_name), access=access)

    state = RtuAllocState()
    leaves = [standalone("Line_Speed", "INT"),
              standalone("Raw_Count", "UINT")]
    assignments, _ = allocate_rtu(state, leaves, set(), {}, set(),
                                  program_names={"Line_Speed", "Raw_Count"})
    by_path = {a.leaf.full_path: a.entry for a in assignments}
    assert by_path["Line_Speed"].name == "Line_Speed_Obj"
    assert by_path["Line_Speed"].direct is False
    assert by_path["Raw_Count"].name == "Raw_Count_Obj"

    st = generate_rtu_st(assignments, timestamp="t0")
    assert "Line_Speed_Obj.value := Line_Speed;" in st
    assert "Raw_Count_Obj.value := UINT_TO_INT(Raw_Count);" in st


def test_stale_direct_entries_are_retired():
    """Sidecars from before the .value discovery hold direct-bound entries
    (object name == program variable); they must be retired and replaced
    with _Obj-named copies."""
    state = RtuAllocState()
    leaf = FlatLeaf(instance="Line_Speed", rel_path="", type_name="INT",
                    kind=leaf_kind("INT"), access=Access.READ)
    state.entries["Line_Speed"] = RtuEntry(
        "Line_Speed", DT_ANALOG, LVT_INT, GROUP_AI, "read", "word1", "INT",
        dnp3_point=2400, register=40800, direct=True)
    assignments, warnings = allocate_rtu(state, [leaf], set(), {}, set())
    entry = assignments[0].entry
    assert entry.name == "Line_Speed_Obj" and entry.direct is False
    assert state.dead and state.dead[0]["name"] == "Line_Speed"
    assert any("retired" in w for w in warnings)


def test_point_floor_lifts_new_points():
    """RemoteConnect auto-assigns points to objects exported without one and
    rejects colliding explicit points (import-probed on the 474): every new
    point must sit above the workbook's total object count."""
    state = RtuAllocState()
    assignments, _ = allocate_rtu(state, _leaves(), set(), {}, set(),
                                  point_floor=2306)
    points = [a.entry.dnp3_point for a in assignments]
    assert min(points) >= 2306
    by_path = {a.leaf.full_path: a.entry for a in assignments}
    assert by_path["Pump1.Cmd"].dnp3_point == 2306      # g1 space
    assert by_path["Pump1.Running"].dnp3_point == 2307
    assert by_path["Pump1.Flow_PV"].dnp3_point == 2306  # g30 space
    # registers are NOT floored (probed working at low numbers)
    assert by_path["Pump1.Cmd"].register == 1


def test_foreign_collision_renumbers_unlanded_and_warns_on_live():
    """Duplicate-address handling: an engineer may hand-create objects in
    RemoteConnect using numbers our sidecar assigned earlier."""
    state = RtuAllocState()
    allocate_rtu(state, _leaves(), set(), {}, set())
    cmd_reg = state.entries["Pump1.Cmd"].register        # coil 1
    flow_pt = state.entries["Pump1.Flow_PV"].dnp3_point  # g30 point 1
    # foreign objects now use those same numbers; Pump1.Cmd landed in the
    # RTU (present in workbook), Pump1.Flow_PV did not
    assignments, warnings = allocate_rtu(
        state, _leaves(), {"foreign_di", "foreign_ai"}, {}, set(),
        workbook_names={"pump1_cmd", "foreign_di", "foreign_ai"},
        foreign_points={"g30": {flow_pt}}, foreign_registers={cmd_reg})
    by_path = {a.leaf.full_path: a.entry for a in assignments}
    # never landed -> renumbered away from the foreign duplicate
    assert by_path["Pump1.Flow_PV"].dnp3_point != flow_pt
    # landed -> kept, reported as a live duplicate
    assert by_path["Pump1.Cmd"].register == cmd_reg
    assert any("share an address" in w for w in warnings)
    assert any("renumbered" in w for w in warnings)


def test_rejected_low_points_renumbered_live_points_kept():
    state = RtuAllocState()
    allocate_rtu(state, _leaves(), set(), {}, set())  # low points (bad run)
    low = state.entries["Pump1.Running"].dnp3_point
    assert low < 100
    # 'Pump1_Cmd' made it into the RTU (present in workbook); Running didn't
    assignments, warnings = allocate_rtu(
        state, _leaves(), set(), {}, set(),
        point_floor=2306, workbook_names={"pump1_cmd"})
    by_path = {a.leaf.full_path: a.entry for a in assignments}
    assert by_path["Pump1.Cmd"].dnp3_point == 1          # live: untouched
    assert by_path["Pump1.Running"].dnp3_point >= 2306   # rejected: lifted
    assert by_path["Pump1.Running"].name == "Pump1_Running"  # name stable
    assert any("renumbered" in w for w in warnings)
    # a third run with the same floor is stable
    again, _ = allocate_rtu(state, _leaves(), set(), {}, set(),
                            point_floor=2306, workbook_names={"pump1_cmd"})
    assert {a.leaf.full_path: a.entry.dnp3_point for a in again} == {
        a.leaf.full_path: a.entry.dnp3_point for a in assignments}


# ---------------------------------------------------------------------- ST

def test_st_directions_and_conversions():
    state = RtuAllocState()
    leaves = [
        _leaf("Pump1.Cmd", "BOOL", Access.READ_WRITE),
        _leaf("Pump1.Running", "BOOL"),
        _leaf("Pump1.Cnt", "UINT", Access.READ_WRITE),
        _leaf("Pump1.Sts", "WORD"),
    ]
    assignments, _ = allocate_rtu(state, leaves, set(), {}, set())
    st = generate_rtu_st(assignments, project="p", timestamp="t0")
    assert "Pump1.Cmd := Pump1_Cmd.value;" in st
    assert "Pump1_Running.value := Pump1.Running;" in st
    assert "Pump1.Cnt := INT_TO_UINT(Pump1_Cnt.value);" in st
    assert "Pump1_Sts.value := WORD_TO_INT(Pump1.Sts);" in st
    # Read/Write copies precede Read copies
    assert st.index("Pump1.Cmd :=") < st.index("Pump1_Running.value :=")
