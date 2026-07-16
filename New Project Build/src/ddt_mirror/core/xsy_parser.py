"""Parse a Control Expert variables export (.xsy / VariablesExchangeFile).

One export_xml('variables') call yields everything we need:
- <DDTSource DDTName="X"><structure><variables .../></structure></DDTSource>
  = DDT type definitions (flat member list; nested DDTs are BY REFERENCE:
    a member's typeName names another DDT).
- <dataBlock><variables name=".." typeName=".." topologicalAddress=".."/>
  = global variable declarations (DDT instances and standalone tags).

Only root-level dataBlock elements are global variables; FBSource (DFB)
bodies carry their own parameter/local blocks and must be ignored.
"""

from __future__ import annotations

import os

from lxml import etree

from .model import DdtMember, DdtType, Tag


def _text(el, child: str) -> str:
    sub = el.find(child)
    return (sub.text or "").strip() if sub is not None else ""


def parse_xsy(xml_text: str) -> tuple[dict[str, DdtType], list[Tag]]:
    """Return ({ddt type name: DdtType}, [global Tag])."""
    root = etree.fromstring(xml_text.encode("utf-8"))

    types: dict[str, DdtType] = {}
    for src in root.findall("DDTSource"):
        name = src.get("DDTName", "")
        if not name:
            continue
        members = [
            DdtMember(
                name=var.get("name", ""),
                type_name=var.get("typeName", ""),
                comment=_text(var, "comment"),
            )
            for var in src.findall("structure/variables")
            if var.get("name")
        ]
        types[name] = DdtType(name=name, members=members)

    tags: list[Tag] = []
    for block in root.findall("dataBlock"):
        for var in block.findall("variables"):
            name = var.get("name", "")
            if not name:
                continue
            tags.append(
                Tag(
                    name=name,
                    type_name=var.get("typeName", ""),
                    comment=_text(var, "comment"),
                    address=var.get("topologicalAddress", ""),
                )
            )
    return types, tags


def fetch_variables_xml(bridge) -> str:
    """The open project's variables export (.xsy) as text.

    Handles both export_xml return shapes: inline xml, or a temp-file path
    when the export exceeds the bridge's inline size limit (400 KB).
    """
    result = bridge.export_xml("variables", None, None)
    if result.get("too_large_inline"):
        with open(result["file"], "r", encoding="utf-8-sig", errors="replace") as fh:
            xml_text = fh.read()
        try:
            os.unlink(result["file"])
        except OSError:
            pass
        return xml_text
    return result["xml"]


def load_project_variables(bridge) -> tuple[dict[str, DdtType], list[Tag]]:
    """Fetch and parse all variables from the currently open CE project."""
    return parse_xsy(fetch_variables_xml(bridge))
