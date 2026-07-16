"""Core data model: tag types, flattened leaves, allocation results.

The type→size tables mirror control_expert_mcp.modbus (the authoritative
codec the generated addresses must stay compatible with).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class Access(str, Enum):
    READ = "read"              # HMI displays only:  address := Tag
    READ_WRITE = "read_write"  # HMI writes:         Tag := address


class LeafKind(str, Enum):
    BIT = "bit"      # BOOL/EBOOL  -> one %M coil, direct literal in ST
    WORD1 = "word1"  # INT/UINT/WORD -> one %MW, direct literal in ST
    WORD2 = "word2"  # REAL/32-bit -> two %MW via a created located variable


# Must match _DWORD_TYPES / _WORD_TYPES / _BOOL_TYPES in control_expert_mcp.modbus.
TWO_WORD_TYPES = {"DINT", "UDINT", "DWORD", "REAL", "TIME", "DATE", "TOD", "DT"}
ONE_WORD_TYPES = {"INT", "UINT", "WORD"}
BIT_TYPES = {"BOOL", "EBOOL"}

_ARRAY_RE = re.compile(r"^\s*ARRAY\s*\[", re.IGNORECASE)
_STRING_RE = re.compile(r"^\s*STRING", re.IGNORECASE)


def leaf_kind(type_name: str) -> LeafKind | None:
    """Map an elementary IEC type to its mirror kind; None = unsupported."""
    t = type_name.strip().upper()
    if t in BIT_TYPES:
        return LeafKind.BIT
    if t in ONE_WORD_TYPES:
        return LeafKind.WORD1
    if t in TWO_WORD_TYPES:
        return LeafKind.WORD2
    return None


def is_array_type(type_name: str) -> bool:
    return bool(_ARRAY_RE.match(type_name))


def is_string_type(type_name: str) -> bool:
    return bool(_STRING_RE.match(type_name))


@dataclass
class DdtMember:
    name: str
    type_name: str
    comment: str = ""


@dataclass
class DdtType:
    name: str
    members: list[DdtMember] = field(default_factory=list)


@dataclass
class Tag:
    """A global variable: a DDT instance or a standalone elementary tag."""

    name: str
    type_name: str
    comment: str = ""
    address: str = ""  # existing TopologicalAddress, if already located


@dataclass
class FlatLeaf:
    """One mirrorable elementary value."""

    instance: str        # "Pump1" (or the tag name itself for standalone tags)
    rel_path: str        # "Ctrl.Man_SP"; "" for standalone tags
    type_name: str       # elementary IEC type
    kind: LeafKind
    ddt_type: str = ""   # declared DDT type of the instance; "" for standalone
    access: Access = Access.READ
    comment: str = ""
    located: str = ""    # existing address if the source tag is already located

    @property
    def full_path(self) -> str:
        """Exact ST lvalue, e.g. 'Pump1.Ctrl.Man_SP'."""
        return f"{self.instance}.{self.rel_path}" if self.rel_path else self.instance

    @property
    def needs_var(self) -> bool:
        """True when the mirror must be a created located variable.

        All 2-word types (no direct %MW assignment in ST), plus 1-word
        UINT/WORD: a bare %MWi literal is INT-typed and CE rejects the
        implicit WORD/UINT<->INT assignment (E1092). Only INT and
        BOOL/EBOOL leaves can use direct %MW / %M literals.
        """
        if self.kind is LeafKind.WORD2:
            return True
        return self.type_name.strip().upper() in {"UINT", "WORD"}

    @property
    def access_key(self) -> str:
        """Key for user access overrides: per DDT *type* (all instances) or per tag."""
        if self.ddt_type:
            return f"{self.ddt_type}|{self.rel_path}"
        return f"|{self.instance}"

    @property
    def instance_access_key(self) -> str:
        """Per-variable access override key (manual mode); beats access_key."""
        return f"!{self.full_path}"


@dataclass
class Assignment:
    leaf: FlatLeaf
    address: str          # "%M100" or "%MW1000"
    mirror_var: str = ""  # created located variable name (WORD2 only)
    premapped: bool = False  # tag already located there — no mirror, no ST copy

    @property
    def registers(self) -> int:
        return 2 if self.leaf.kind is LeafKind.WORD2 else 1


@dataclass
class MirrorPlan:
    assignments: list[Assignment] = field(default_factory=list)
    new_variables: list[dict] = field(default_factory=list)  # create_variable kwargs
    st_source: str = ""
    csv_text: str = ""
    warnings: list[str] = field(default_factory=list)
