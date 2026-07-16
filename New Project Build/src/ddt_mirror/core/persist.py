"""Sidecar persistence: <project stem>.hmimirror.json next to the .stu.

Holds everything that must survive between runs: settings, type/member
selections, access overrides, and the append-only allocation state. Lives
next to the project so it travels with it and diffs in version control.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from .allocator import AllocState
from .rtu import RtuAllocState

SIDECAR_VERSION = 2


@dataclass
class Settings:
    base_bit: int = 100        # first %M for BOOL mirrors
    base_word: int = 1000      # first %MW for word/REAL mirrors
    section_name: str = "HMI_MIRROR"
    task_name: str = "MAST"
    var_prefix: str = "HMI_"
    type_level_edit: bool = True  # member/access edits apply to the whole DDT type
    hmi_index_base: int = 0       # HMI address = RTU register - 40001 + base

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @staticmethod
    def from_dict(d: dict) -> "Settings":
        s = Settings()
        for k, v in d.items():
            if hasattr(s, k):
                setattr(s, k, v)
        return s


@dataclass
class SidecarState:
    settings: Settings = field(default_factory=Settings)
    selected_types: list[str] = field(default_factory=list)   # DDT/elementary type names
    deselected_leaves: list[str] = field(default_factory=list)  # full_paths unchecked by user
    # "TYPE|rel.path" member exclusions: apply to EVERY instance of the DDT
    # type, including instances added to the project later.
    deselected_type_members: list[str] = field(default_factory=list)
    access_overrides: dict[str, str] = field(default_factory=dict)  # access_key -> Access value
    alloc: AllocState = field(default_factory=AllocState)
    rtu: RtuAllocState = field(default_factory=RtuAllocState)

    def to_dict(self) -> dict:
        return {
            "version": SIDECAR_VERSION,
            "settings": self.settings.to_dict(),
            "selected_types": self.selected_types,
            "deselected_leaves": self.deselected_leaves,
            "deselected_type_members": self.deselected_type_members,
            "access_overrides": self.access_overrides,
            "alloc": self.alloc.to_dict(),
            "rtu": self.rtu.to_dict(),
        }

    @staticmethod
    def from_dict(d: dict) -> "SidecarState":
        return SidecarState(
            settings=Settings.from_dict(d.get("settings", {})),
            selected_types=list(d.get("selected_types", [])),
            deselected_leaves=list(d.get("deselected_leaves", [])),
            deselected_type_members=list(d.get("deselected_type_members", [])),
            access_overrides=dict(d.get("access_overrides", {})),
            alloc=AllocState.from_dict(d.get("alloc", {})),
            rtu=RtuAllocState.from_dict(d.get("rtu", {})),
        )


def sidecar_path(project_path: str) -> str:
    stem, _ = os.path.splitext(project_path)
    return stem + ".hmimirror.json"


def load_sidecar(project_path: str) -> SidecarState:
    path = sidecar_path(project_path)
    if not os.path.isfile(path):
        return SidecarState()
    with open(path, "r", encoding="utf-8") as fh:
        return SidecarState.from_dict(json.load(fh))


def save_sidecar(project_path: str, state: SidecarState) -> str:
    path = sidecar_path(project_path)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state.to_dict(), fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)
    return path
