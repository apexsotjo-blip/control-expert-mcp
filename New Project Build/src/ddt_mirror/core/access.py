"""HMI access presets: Read (display only) vs Read/Write (HMI can write).

Preset by naming convention on the leaf member name; user overrides are
stored per DDT type + member path (so they apply to every instance of that
type) or per standalone tag name — see FlatLeaf.access_key.
"""

from __future__ import annotations

import re

from .model import Access, FlatLeaf

# Cmd*/Man* prefixes and *_SP suffix are HMI-written by convention.
RW_NAME_PATTERN = re.compile(r"^(cmd|man)|_sp$", re.IGNORECASE)


def guess_access(member_name: str) -> Access:
    leaf_name = member_name.rsplit(".", 1)[-1]
    if RW_NAME_PATTERN.search(leaf_name):
        return Access.READ_WRITE
    return Access.READ


def apply_access(leaves: list[FlatLeaf], overrides: dict[str, str]) -> None:
    """Set each leaf's access: per-variable override > per-type override > guess."""
    for leaf in leaves:
        override = (overrides.get(leaf.instance_access_key)
                    or overrides.get(leaf.access_key))
        if override:
            leaf.access = Access(override)
        else:
            leaf.access = guess_access(leaf.rel_path or leaf.instance)
