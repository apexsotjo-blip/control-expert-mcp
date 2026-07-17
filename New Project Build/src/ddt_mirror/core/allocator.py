"""Stable %M / %MW address allocation.

Stability is the contract: once a leaf is assigned an address it is NEVER
moved or reused for something else — regenerating must not reshuffle the map
or every HMI screen breaks. Consequences:

- Existing assignments (same kind + access) are reused verbatim.
- Deselected leaves keep their entry, tombstoned; reselecting revives the
  same address.
- A kind/access change gets a NEW slot; the old one goes to the dead list
  (a stale HMI then reads a dead register, never a wrong live value).
- Addresses only ever grow from the high-water marks; dead/tombstoned slots
  are never recycled.

Tags already located at a Modbus-reachable %M/%MW address are passed through
with their existing address (no mirror, no ST copy).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .model import Assignment, FlatLeaf, LeafKind

_REACHABLE_RE = re.compile(r"^%(M|MW)(\d+)$", re.IGNORECASE)


@dataclass
class AllocEntry:
    address: str  # "%M100" / "%MW1000"
    kind: str     # LeafKind value
    access: str   # Access value
    active: bool = True

    def to_dict(self) -> dict:
        return {"address": self.address, "kind": self.kind,
                "access": self.access, "active": self.active}

    @staticmethod
    def from_dict(d: dict) -> "AllocEntry":
        return AllocEntry(d["address"], d["kind"], d["access"],
                          bool(d.get("active", True)))


@dataclass
class AllocState:
    base_bit: int = 100
    base_word: int = 1000
    next_bit: int | None = None   # high-water marks; None until first use
    next_word: int | None = None
    leaves: dict[str, AllocEntry] = field(default_factory=dict)  # key = full_path
    dead: list[dict] = field(default_factory=list)  # superseded slots, never reused

    def to_dict(self) -> dict:
        return {
            "base_bit": self.base_bit,
            "base_word": self.base_word,
            "next_bit": self.next_bit,
            "next_word": self.next_word,
            "leaves": {k: e.to_dict() for k, e in self.leaves.items()},
            "dead": self.dead,
        }

    @staticmethod
    def from_dict(d: dict) -> "AllocState":
        return AllocState(
            base_bit=int(d.get("base_bit", 100)),
            base_word=int(d.get("base_word", 1000)),
            next_bit=d.get("next_bit"),
            next_word=d.get("next_word"),
            leaves={k: AllocEntry.from_dict(e) for k, e in d.get("leaves", {}).items()},
            dead=list(d.get("dead", [])),
        )


def premapped_address(leaf: FlatLeaf) -> str | None:
    """Existing Modbus-reachable address of an already-located standalone tag."""
    if leaf.rel_path:
        return None  # located DDT instances: member offsets are not derivable
    m = _REACHABLE_RE.match(leaf.located.strip()) if leaf.located else None
    return f"%{m.group(1).upper()}{m.group(2)}" if m else None


def allocate(
    state: AllocState,
    selected: list[FlatLeaf],
    bit_floor: int = 0,
    word_floor: int = 0,
    word_bools: bool = False,
) -> list[Assignment]:
    """Assign addresses to the selected leaves, mutating state append-only.

    `bit_floor`/`word_floor` raise the starting point for NEW slots above
    addresses the project already uses elsewhere (located variables and
    code literals — brownfield safety). Existing entries are never moved.

    `word_bools` places BOOL leaves in %MW word space (one word each,
    BOOL_TO_INT-copied) instead of %M coils — required when a Modbus
    scanner upstream can only read registers (SCADAPack T2 topology).
    """
    if state.next_bit is None:
        state.next_bit = state.base_bit
    if state.next_word is None:
        state.next_word = state.base_word
    state.next_bit = max(state.next_bit, bit_floor)
    state.next_word = max(state.next_word, word_floor)

    assignments: list[Assignment] = []
    selected_keys = set()

    for leaf in selected:
        key = leaf.full_path
        selected_keys.add(key)

        pre = premapped_address(leaf)
        if pre:
            assignments.append(Assignment(leaf=leaf, address=pre, premapped=True))
            continue

        entry = state.leaves.get(key)
        if entry and (entry.kind != leaf.kind.value or entry.access != leaf.access.value):
            state.dead.append({"key": key, **entry.to_dict()})
            entry = None
        if entry is None:
            if leaf.kind is LeafKind.BIT and word_bools:
                address = f"%MW{state.next_word}"
                state.next_word += 1
            elif leaf.kind is LeafKind.BIT:
                address = f"%M{state.next_bit}"
                state.next_bit += 1
            elif leaf.kind is LeafKind.WORD1:
                address = f"%MW{state.next_word}"
                state.next_word += 1
            else:  # WORD2: two consecutive registers, low word first
                # CE alignment constraint: 32-bit located variables must start
                # on an EVEN %MW index (M340/M580) — pad one word if needed.
                if state.next_word % 2:
                    state.next_word += 1
                address = f"%MW{state.next_word}"
                state.next_word += 2
            entry = AllocEntry(address, leaf.kind.value, leaf.access.value)
            state.leaves[key] = entry
        entry.active = True
        assignments.append(Assignment(leaf=leaf, address=entry.address))

    for key, entry in state.leaves.items():
        if key not in selected_keys:
            entry.active = False

    return assignments
