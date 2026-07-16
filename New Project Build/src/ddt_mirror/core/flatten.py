"""Flatten DDT instances (and standalone elementary tags) to mirrorable leaves.

Nested DDTs are resolved by reference (a member's typeName names another DDT).
Arrays, STRINGs, and unknown types (DFB refs, IODDTs) are skipped with a
warning; a visited-type stack guards against recursive DDT definitions.
"""

from __future__ import annotations

from .model import DdtType, FlatLeaf, Tag, is_array_type, is_string_type, leaf_kind


def flatten_tags(
    tags: list[Tag], types: dict[str, DdtType]
) -> tuple[list[FlatLeaf], list[str]]:
    """Expand every tag to elementary FlatLeaf entries.

    Returns (leaves, warnings). Leaves keep source-declaration order.
    """
    leaves: list[FlatLeaf] = []
    warnings: list[str] = []

    for tag in tags:
        if tag.type_name in types:
            _expand(
                instance=tag.name,
                ddt_type=tag.type_name,
                current_type=tag.type_name,
                prefix="",
                instance_located=tag.address,
                types=types,
                stack=[],
                leaves=leaves,
                warnings=warnings,
            )
            continue
        kind = leaf_kind(tag.type_name)
        if kind is not None:
            leaves.append(
                FlatLeaf(
                    instance=tag.name,
                    rel_path="",
                    type_name=tag.type_name,
                    kind=kind,
                    comment=tag.comment,
                    located=tag.address,
                )
            )
        elif is_array_type(tag.type_name) or is_string_type(tag.type_name):
            warnings.append(
                f"{tag.name}: type '{tag.type_name}' not supported yet (skipped)"
            )
        # Other unknown types (DFB instances, IODDTs, ...) are silently out of
        # scope — they are not HMI data tags.
    return leaves, warnings


def _expand(
    instance: str,
    ddt_type: str,
    current_type: str,
    prefix: str,
    instance_located: str,
    types: dict[str, DdtType],
    stack: list[str],
    leaves: list[FlatLeaf],
    warnings: list[str],
) -> None:
    if current_type in stack:
        warnings.append(
            f"{instance}.{prefix}: recursive DDT '{current_type}' (skipped)"
        )
        return
    stack.append(current_type)
    try:
        for member in types[current_type].members:
            rel = f"{prefix}.{member.name}" if prefix else member.name
            if member.type_name in types:
                _expand(
                    instance, ddt_type, member.type_name, rel, instance_located,
                    types, stack, leaves, warnings,
                )
                continue
            kind = leaf_kind(member.type_name)
            if kind is not None:
                leaves.append(
                    FlatLeaf(
                        instance=instance,
                        rel_path=rel,
                        type_name=member.type_name,
                        kind=kind,
                        ddt_type=ddt_type,
                        comment=member.comment,
                        located=instance_located,
                    )
                )
            elif is_array_type(member.type_name) or is_string_type(member.type_name):
                warnings.append(
                    f"{instance}.{rel}: type '{member.type_name}' not supported yet (skipped)"
                )
            else:
                warnings.append(
                    f"{instance}.{rel}: unknown type '{member.type_name}' (skipped)"
                )
    finally:
        stack.pop()
