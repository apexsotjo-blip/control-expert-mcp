from ddt_mirror.core.access import apply_access, guess_access
from ddt_mirror.core.model import Access, FlatLeaf, LeafKind


def test_naming_convention_presets():
    assert guess_access("Cmd") is Access.READ_WRITE
    assert guess_access("Cmd_Start") is Access.READ_WRITE
    assert guess_access("Man_Mode") is Access.READ_WRITE
    assert guess_access("Flow_SP") is Access.READ_WRITE
    assert guess_access("Ctrl.Man_SP") is Access.READ_WRITE  # leaf segment decides
    assert guess_access("Running") is Access.READ
    assert guess_access("Flow_PV") is Access.READ
    assert guess_access("Speed") is Access.READ


def _leaf(instance, rel, ddt=""):
    return FlatLeaf(instance=instance, rel_path=rel, type_name="INT",
                    kind=LeafKind.WORD1, ddt_type=ddt)


def test_overrides_are_per_ddt_type():
    l1 = _leaf("Pump1", "Running", "PUMP_T")
    l2 = _leaf("Pump2", "Running", "PUMP_T")
    standalone = _leaf("Line_Speed", "")
    overrides = {"PUMP_T|Running": "read_write", "|Line_Speed": "read_write"}
    apply_access([l1, l2, standalone], overrides)
    assert l1.access is Access.READ_WRITE  # override beats guess...
    assert l2.access is Access.READ_WRITE  # ...for every instance of the type
    assert standalone.access is Access.READ_WRITE


def test_per_variable_override_beats_type_override():
    l1 = _leaf("Pump1", "Running", "PUMP_T")
    l2 = _leaf("Pump2", "Running", "PUMP_T")
    overrides = {
        "PUMP_T|Running": "read_write",   # type level: all instances R/W
        "!Pump2.Running": "read",         # manual override just for Pump2
    }
    apply_access([l1, l2], overrides)
    assert l1.access is Access.READ_WRITE
    assert l2.access is Access.READ


def test_guess_used_without_override():
    l1 = _leaf("Pump1", "Cmd", "PUMP_T")
    l2 = _leaf("Pump1", "Running", "PUMP_T")
    apply_access([l1, l2], {})
    assert l1.access is Access.READ_WRITE
    assert l2.access is Access.READ
