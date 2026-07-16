from ddt_mirror.core.allocator import AllocState, allocate
from ddt_mirror.core.model import Access, FlatLeaf, LeafKind


def _leaf(name, kind, access=Access.READ, located="", rel=""):
    return FlatLeaf(instance=name, rel_path=rel, type_name="X", kind=kind,
                    access=access, located=located)


def test_dense_allocation_and_word2_spacing():
    state = AllocState(base_bit=100, base_word=1000)
    leaves = [
        _leaf("B1", LeafKind.BIT),
        _leaf("W1", LeafKind.WORD1),
        _leaf("R1", LeafKind.WORD2),
        _leaf("R2", LeafKind.WORD2),
        _leaf("W2", LeafKind.WORD1),
        _leaf("B2", LeafKind.BIT),
    ]
    a = {x.leaf.instance: x.address for x in allocate(state, leaves)}
    # 32-bit values are even-aligned (CE alignment constraint): after W1 takes
    # %MW1000, R1 skips odd 1001 and lands on 1002.
    assert a == {"B1": "%M100", "B2": "%M101",
                 "W1": "%MW1000", "R1": "%MW1002", "R2": "%MW1004", "W2": "%MW1006"}


def test_word2_even_alignment():
    state = AllocState(base_word=999)  # odd base: first WORD2 must pad to 1000
    first = allocate(state, [_leaf("R1", LeafKind.WORD2)])
    assert first[0].address == "%MW1000"

    state2 = AllocState(base_word=1000)
    result = allocate(state2, [
        _leaf("W1", LeafKind.WORD1),   # 1000
        _leaf("T1", LeafKind.WORD2),   # pad 1001 -> 1002..1003
        _leaf("W2", LeafKind.WORD1),   # 1004
        _leaf("T2", LeafKind.WORD2),   # pad 1005 -> 1006..1007
    ])
    by = {x.leaf.instance: x.address for x in result}
    assert by == {"W1": "%MW1000", "T1": "%MW1002",
                  "W2": "%MW1004", "T2": "%MW1006"}
    assert all(int(a[3:]) % 2 == 0 for k, a in by.items() if k.startswith("T"))


def test_rerun_is_stable():
    state = AllocState()
    leaves = [_leaf("A", LeafKind.WORD2), _leaf("B", LeafKind.BIT)]
    first = [(x.leaf.instance, x.address) for x in allocate(state, leaves)]
    second = [(x.leaf.instance, x.address) for x in allocate(state, leaves)]
    assert first == second


def test_new_leaf_appends_never_reshuffles():
    state = AllocState(base_word=1000)
    allocate(state, [_leaf("A", LeafKind.WORD1)])
    result = allocate(state, [_leaf("NEW", LeafKind.WORD1), _leaf("A", LeafKind.WORD1)])
    by = {x.leaf.instance: x.address for x in result}
    assert by["A"] == "%MW1000"    # existing kept even though NEW came first
    assert by["NEW"] == "%MW1001"  # appended at high-water mark


def test_deselect_tombstones_and_reselect_revives():
    state = AllocState()
    allocate(state, [_leaf("A", LeafKind.WORD1), _leaf("B", LeafKind.WORD1)])
    addr_b = state.leaves["B"].address

    allocate(state, [_leaf("A", LeafKind.WORD1)])  # B deselected
    assert state.leaves["B"].active is False
    assert state.leaves["B"].address == addr_b  # address retained

    result = allocate(state, [_leaf("A", LeafKind.WORD1), _leaf("B", LeafKind.WORD1)])
    by = {x.leaf.instance: x.address for x in result}
    assert by["B"] == addr_b  # revived at the same address
    assert state.leaves["B"].active is True


def test_access_flip_gets_new_slot_old_goes_dead():
    state = AllocState(base_word=1000)
    allocate(state, [_leaf("A", LeafKind.WORD1, Access.READ)])
    old = state.leaves["A"].address

    result = allocate(state, [_leaf("A", LeafKind.WORD1, Access.READ_WRITE)])
    new = result[0].address
    assert new != old
    assert any(d["key"] == "A" and d["address"] == old for d in state.dead)

    # dead address is never handed out again
    more = allocate(state, [_leaf("A", LeafKind.WORD1, Access.READ_WRITE),
                            _leaf("Z", LeafKind.WORD1)])
    assert all(x.address != old for x in more)


def test_prelocated_tag_passthrough():
    state = AllocState()
    result = allocate(state, [
        _leaf("Located_Speed", LeafKind.WORD1, located="%MW50"),
        _leaf("Input_Word", LeafKind.WORD1, located="%IW0.2"),  # not Modbus-reachable
    ])
    by = {x.leaf.instance: x for x in result}
    assert by["Located_Speed"].premapped and by["Located_Speed"].address == "%MW50"
    assert not by["Input_Word"].premapped  # gets a fresh mirror instead
    assert "Located_Speed" not in state.leaves  # no allocation burned


def test_located_ddt_member_is_not_passthrough():
    state = AllocState()
    leaf = _leaf("Pump1", LeafKind.WORD1, located="%MW10", rel="Ctrl.Mode")
    result = allocate(state, [leaf])
    assert not result[0].premapped  # member offsets are not derivable
