import os

from ddt_mirror.core.ddt_library import (
    DdtLibrary, DdtTypeConfig, apply_library, capture_type, library_path,
    load_library, project_ddt_types, save_library,
)
from ddt_mirror.core.engine import ProjectData
from ddt_mirror.core.model import Access, FlatLeaf, leaf_kind
from ddt_mirror.core.persist import SidecarState


def _leaf(instance, rel, type_name, ddt_type):
    return FlatLeaf(instance=instance, rel_path=rel, type_name=type_name,
                    kind=leaf_kind(type_name), ddt_type=ddt_type)


def _data():
    # two PUMP_T instances + one standalone tag
    leaves = [
        _leaf("Pump1", "Cmd", "BOOL", "PUMP_T"),
        _leaf("Pump1", "Man_SP", "REAL", "PUMP_T"),
        _leaf("Pump1", "Flow_PV", "REAL", "PUMP_T"),
        _leaf("Pump2", "Cmd", "BOOL", "PUMP_T"),
        _leaf("Pump2", "Man_SP", "REAL", "PUMP_T"),
        _leaf("Pump2", "Flow_PV", "REAL", "PUMP_T"),
        FlatLeaf(instance="Line_Speed", rel_path="", type_name="INT",
                 kind=leaf_kind("INT")),
    ]
    return ProjectData(leaves=leaves)


def test_library_roundtrip(tmp_path):
    path = str(tmp_path / "lib.json")
    lib = DdtLibrary(types={"PUMP_T": DdtTypeConfig(
        access={"Cmd": "read_write", "Flow_PV": "read"},
        deselected=["Flow_PV"])})
    save_library(lib, path)
    back = load_library(path)
    assert back.types["PUMP_T"].access == {"Cmd": "read_write",
                                           "Flow_PV": "read"}
    assert back.types["PUMP_T"].deselected == ["Flow_PV"]


def test_load_missing_returns_empty(tmp_path):
    assert load_library(str(tmp_path / "nope.json")).types == {}


def test_project_ddt_types_lists_ddts_only():
    assert project_ddt_types(_data()) == ["PUMP_T"]


def test_capture_type_records_effective_access_and_deselection():
    data, state = _data(), SidecarState()
    # user forced Flow_PV to R/W (guess would be Read) and unchecked Cmd
    state.access_overrides["PUMP_T|Flow_PV"] = Access.READ_WRITE.value
    state.deselected_type_members.append("PUMP_T|Cmd")
    lib = DdtLibrary()
    cfg = capture_type(lib, "PUMP_T", data, state)
    # Cmd guessed R/W, Man_SP guessed R/W (_SP), Flow_PV overridden R/W
    assert cfg.access == {
        "Cmd": "read_write", "Man_SP": "read_write", "Flow_PV": "read_write"}
    assert cfg.deselected == ["Cmd"]
    assert lib.types["PUMP_T"] is cfg


def test_apply_library_is_authoritative_for_matched_types():
    data, state = _data(), SidecarState()
    # stale project config that must be overwritten
    state.access_overrides["PUMP_T|Cmd"] = Access.READ.value
    state.deselected_type_members.append("PUMP_T|Man_SP")
    lib = DdtLibrary(types={"PUMP_T": DdtTypeConfig(
        access={"Cmd": "read_write", "Flow_PV": "read"},
        deselected=["Flow_PV"])})
    applied = apply_library(lib, data, state)
    assert applied == ["PUMP_T"]
    assert state.access_overrides["PUMP_T|Cmd"] == "read_write"   # overwritten
    assert state.access_overrides["PUMP_T|Flow_PV"] == "read"
    # deselection reconciled to the saved set (Man_SP removed, Flow_PV added)
    assert "PUMP_T|Man_SP" not in state.deselected_type_members
    assert "PUMP_T|Flow_PV" in state.deselected_type_members


def test_apply_library_ignores_unsaved_types_and_other_keys():
    data, state = _data(), SidecarState()
    state.access_overrides["OTHER_T|X"] = "read"          # different type
    state.access_overrides["!Pump1.Cmd"] = "read"          # per-variable
    lib = DdtLibrary()  # empty
    assert apply_library(lib, data, state) == []
    assert state.access_overrides == {"OTHER_T|X": "read",
                                      "!Pump1.Cmd": "read"}


def test_capture_then_apply_reproduces_config(tmp_path):
    data = _data()
    src = SidecarState()
    src.access_overrides["PUMP_T|Flow_PV"] = Access.READ_WRITE.value
    src.deselected_type_members.append("PUMP_T|Man_SP")
    lib = DdtLibrary()
    capture_type(lib, "PUMP_T", data, src)
    path = save_library(lib, str(tmp_path / "l.json"))

    fresh = SidecarState()
    apply_library(load_library(path), data, fresh)
    assert fresh.access_overrides["PUMP_T|Flow_PV"] == "read_write"
    assert "PUMP_T|Man_SP" in fresh.deselected_type_members


def test_library_path_env_override(monkeypatch, tmp_path):
    target = str(tmp_path / "custom.json")
    monkeypatch.setenv("DDT_MIRROR_LIBRARY", target)
    assert library_path() == target
