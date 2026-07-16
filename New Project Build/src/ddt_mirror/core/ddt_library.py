"""Global, cross-project DDT read/write configuration library.

The per-project sidecar keeps a project's type-level member config
(access_overrides["TYPE|rel.path"] and deselected_type_members). Engineers
reuse the same DDT types across many projects and do not want to redo the
Read / Read-Write choice each time, so this library stores that config
GLOBALLY, keyed by DDT type NAME, in one file under the user profile.

On project open the library is applied to the sidecar for every DDT type
whose name matches a saved entry, so the members page already shows the
saved access/selection. A "save as default" action captures the current
type-level config back into the library.

Precedence: the library is authoritative for the DDT types it covers - on
open it overwrites the sidecar's type-level entries for those types (the
whole point is one source of truth for a DDT name across projects). It only
touches "TYPE|rel" type-level keys; per-variable (!full_path), per-tag and
allocation state are never affected.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from .access import guess_access
from .persist import SidecarState

LIBRARY_VERSION = 1
_ENV_OVERRIDE = "DDT_MIRROR_LIBRARY"


def library_path() -> str:
    override = os.environ.get(_ENV_OVERRIDE)
    if override:
        return override
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "DdtMirror", "ddt_library.json")


@dataclass
class DdtTypeConfig:
    # member rel.path -> Access value ("read" / "read_write")
    access: dict[str, str] = field(default_factory=dict)
    # member rel.paths excluded (unchecked) for the whole type
    deselected: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"access": self.access, "deselected": self.deselected}

    @staticmethod
    def from_dict(d: dict) -> "DdtTypeConfig":
        return DdtTypeConfig(
            access=dict(d.get("access", {})),
            deselected=list(d.get("deselected", [])),
        )


@dataclass
class DdtLibrary:
    types: dict[str, DdtTypeConfig] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"version": LIBRARY_VERSION,
                "types": {k: v.to_dict() for k, v in self.types.items()}}

    @staticmethod
    def from_dict(d: dict) -> "DdtLibrary":
        return DdtLibrary(types={
            k: DdtTypeConfig.from_dict(v) for k, v in d.get("types", {}).items()
        })


def load_library(path: str | None = None) -> DdtLibrary:
    path = path or library_path()
    if not os.path.isfile(path):
        return DdtLibrary()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return DdtLibrary.from_dict(json.load(fh))
    except (OSError, ValueError):
        return DdtLibrary()


def save_library(lib: DdtLibrary, path: str | None = None) -> str:
    path = path or library_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(lib.to_dict(), fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def _type_member_paths(ddt_type: str, leaves) -> list[str]:
    """Distinct member rel.paths of a DDT type, in first-seen order."""
    seen: set[str] = set()
    rels: list[str] = []
    for leaf in leaves:
        if leaf.ddt_type == ddt_type and leaf.rel_path and \
                leaf.rel_path not in seen:
            seen.add(leaf.rel_path)
            rels.append(leaf.rel_path)
    return rels


def apply_library(lib: DdtLibrary, data, state: SidecarState) -> list[str]:
    """Seed the sidecar's type-level config from the library for every DDT
    type present in `data` that has a saved entry. Returns the applied type
    names. Authoritative: rewrites the matched types' "TYPE|rel" entries."""
    project_types = {leaf.ddt_type for leaf in data.leaves if leaf.ddt_type}
    applied: list[str] = []
    for ddt_type in sorted(project_types):
        cfg = lib.types.get(ddt_type)
        if cfg is None:
            continue
        prefix = f"{ddt_type}|"
        # reconcile the type's member selection to the saved set
        state.deselected_type_members = [
            k for k in state.deselected_type_members if not k.startswith(prefix)]
        for rel in cfg.deselected:
            key = prefix + rel
            if key not in state.deselected_type_members:
                state.deselected_type_members.append(key)
        # set the type's access overrides
        for rel, acc in cfg.access.items():
            state.access_overrides[prefix + rel] = acc
        applied.append(ddt_type)
    return applied


def capture_type(lib: DdtLibrary, ddt_type: str, data,
                 state: SidecarState) -> DdtTypeConfig:
    """Record a DDT type's CURRENT effective type-level config into the
    library (creating/overwriting its entry). Effective access = the
    sidecar's type override if set, else the naming-convention guess, so the
    saved config is complete and independent of the guess heuristic."""
    rels = _type_member_paths(ddt_type, data.leaves)
    deselected_set = set(state.deselected_type_members)
    access: dict[str, str] = {}
    deselected: list[str] = []
    for rel in rels:
        key = f"{ddt_type}|{rel}"
        acc = state.access_overrides.get(key)
        access[rel] = acc if acc is not None else guess_access(rel).value
        if key in deselected_set:
            deselected.append(rel)
    cfg = DdtTypeConfig(access=access, deselected=deselected)
    lib.types[ddt_type] = cfg
    return cfg


def project_ddt_types(data) -> list[str]:
    """DDT type names present in the project (sorted, distinct)."""
    return sorted({leaf.ddt_type for leaf in data.leaves if leaf.ddt_type})
