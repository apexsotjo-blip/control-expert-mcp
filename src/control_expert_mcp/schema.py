"""Pre-validate Control Expert exchange XML against the installed XSD grammar.

The Control Expert install ships the authoritative schema for every exchange
format in ``<install>\\SrcXmlSchema``. Validating a candidate document against
the right XSD catches structural errors instantly — before the slower
import + build round-trip — and steers authoring toward the full, legal element
set instead of the few shapes an author happens to remember.

Schema-dir discovery order: CE_MCP_SCHEMA_DIR env var -> an install path passed
in -> a glob of the standard Schneider install locations.

NOTE: the XSD validates STRUCTURE (element/attribute grammar, enumerations). It
does NOT check semantics like FBD pin geometry, LD cell-count, or type rules —
build_project remains the final oracle.
"""

from __future__ import annotations

import functools
import glob
import os

# Exchange-file root element -> top-level XSD (these pull in the rest via include)
ROOT_TO_XSD = {
    "STExchangeFile": "STExchangeFile.xsd",
    "ILExchangeFile": "ILExchangeFile.xsd",
    "LDExchangeFile": "LDExchangeFile.xsd",
    "FBDExchangeFile": "FBDExchangeFile.xsd",
    "SFCExchangeFile": "SFCExchangeFile.xsd",
    "FBExchangeFile": "FBExchangeFile.xsd",
    "DDTExchangeFile": "DDTExchangeFile.xsd",
    "VariablesExchangeFile": "VariablesExchangeFile.xsd",
    "PGMExchangeFile": "PGMExchangeFile.xsd",
    "FMExchangeFile": "FMExchangeFile.xsd",
    "IOExchangeFile": "IOExchangeFile.xsd",
    "FefExchangeFile": "FefExchangeFile.xsd",
    "FEFExchangeFile": "FefExchangeFile.xsd",
    "TABExchangeFile": "TABExchangeFile.xsd",
    "FDTDTMExchangeFile": "FDTDTMExchangeFile.xsd",
}


class SchemaError(RuntimeError):
    """No schema available, or no mapping for the document root."""


def find_schema_dir(install_path: str | None = None) -> str | None:
    env = os.environ.get("CE_MCP_SCHEMA_DIR")
    if env and os.path.isdir(env):
        return env
    if install_path:
        cand = os.path.join(install_path, "SrcXmlSchema")
        if os.path.isdir(cand):
            return cand
    cands = []
    for pat in (
        r"C:\Program Files (x86)\Schneider Electric\*\SrcXmlSchema",
        r"C:\Program Files\Schneider Electric\*\SrcXmlSchema",
    ):
        cands += [c for c in glob.glob(pat) if os.path.isdir(c)]
    if not cands:
        return None

    def score(p: str):
        low = p.lower()
        # Prefer a real Control Expert / Unity Pro install over other Schneider
        # products (e.g. SCADAPack) that also ship a SrcXmlSchema folder.
        product = 2 if "control expert" in low else 1 if "unity pro" in low else 0
        return (product, p)  # then alphabetical (≈ highest version) as tie-break

    return sorted(cands, key=score)[-1]


@functools.lru_cache(maxsize=32)
def _load_schema(xsd_path: str):
    from lxml import etree

    return etree.XMLSchema(etree.parse(xsd_path))


def validate(xml_text: str, schema_dir: str) -> dict:
    """Return {valid, root, schema, errors}. valid is False on a well-formedness
    or schema violation, with human-readable error lines. For LD documents the
    XSD pass is followed by a few semantic checks (cell-count arithmetic,
    EBOOL-for-edge rules) that the grammar cannot express but build_project
    would otherwise be the first to catch."""
    from lxml import etree

    text = xml_text.lstrip("﻿")
    try:
        doc = etree.fromstring(text.encode("utf-8"))
    except etree.XMLSyntaxError as exc:
        return {"valid": False, "root": None, "schema": None,
                "errors": [f"XML not well-formed: {exc}"]}
    root = etree.QName(doc.tag).localname if doc.tag else None
    xsd_name = ROOT_TO_XSD.get(root)
    if not xsd_name:
        raise SchemaError(
            f"No schema mapped for root <{root}>. Known roots: {sorted(ROOT_TO_XSD)}."
        )
    xsd_path = os.path.join(schema_dir, xsd_name)
    if not os.path.isfile(xsd_path):
        raise SchemaError(f"Schema file not found: {xsd_path}")
    schema = _load_schema(xsd_path)
    ok = schema.validate(doc)
    errors = [f"line {e.line}: {e.message}" for e in schema.error_log]
    # Semantic LD rules only make sense once the structure is grammatical.
    if ok and root == "LDExchangeFile":
        sem = _ld_semantic_errors(doc)
        if sem:
            ok = False
            errors.extend(sem)
    return {"valid": bool(ok), "root": root, "schema": xsd_name, "errors": errors}


# Cell-span per LD element. None = a span this checker can't compute reliably,
# so any line containing it is left to build_project rather than risk a false
# positive (FFBBlock pin columns, multi-row vertical links, etc.).
_LD_FIXED_SPAN = {
    "contact": 1, "coil": 1, "control": 1, "labelCell": 1, "VLink": 1,
}
_LD_WIDTH_ATTR = {"compareBlock": "width", "operateBlock": "width"}
_LD_CELLS_ATTR = {"HLink": "nbCells", "emptyCell": "nbCells"}


def _ld_line_span(line) -> int | None:
    """Total cell-span of one <typeLine>, or None if it contains an element
    whose span this checker does not model (leave such lines to the build)."""
    from lxml import etree

    total = 0
    for el in line:
        tag = etree.QName(el.tag).localname
        if tag == "emptyLine":
            return None  # whole-width rung separator, not a cell row
        if tag in _LD_FIXED_SPAN:
            total += _LD_FIXED_SPAN[tag]
        elif tag in _LD_WIDTH_ATTR:
            total += int(el.get(_LD_WIDTH_ATTR[tag], "0") or "0")
        elif tag in _LD_CELLS_ATTR:
            total += int(el.get(_LD_CELLS_ATTR[tag], "0") or "0")
        elif tag == "shortCircuit":
            sub = _ld_line_span(el)
            if sub is None:
                return None
            total += sub
        else:
            return None  # unknown/unmodelled element (e.g. FFBBlock)
    return total


def _ld_semantic_errors(doc) -> list[str]:
    """Catch the two LD rules the XSD can't: every <typeLine> must fill exactly
    nbColumns cells, and P/N contacts/coils need an EBOOL variable. Conservative
    by design — only flags what it can prove, so it never rejects valid XML."""
    from lxml import etree

    errors: list[str] = []

    # name -> declared typeName, from this file's <dataBlock>. Variables not
    # declared here may be pre-existing project globals, so an edge element on
    # them is left to the build (can't prove the type from the XML alone).
    declared: dict[str, str] = {}
    for v in doc.iter():
        if etree.QName(v.tag).localname == "variables":
            name = v.get("name")
            typ = v.get("typeName")
            if name and typ:
                declared[name] = typ

    for src in doc.iter():
        if etree.QName(src.tag).localname != "LDSource":
            continue
        try:
            ncols = int(src.get("nbColumns", "0") or "0")
        except ValueError:
            ncols = 0
        for line in src.iter():
            if etree.QName(line.tag).localname != "typeLine":
                continue
            span = _ld_line_span(line)
            if span is not None and ncols and span != ncols:
                errors.append(
                    f"line {line.sourceline}: LD cell-count error — this "
                    f"<typeLine> spans {span} cells but nbColumns={ncols}; the "
                    "elements' cell-spans (contacts/coils=1, HLink/emptyCell="
                    "nbCells, compare/operateBlock=width) must sum to nbColumns."
                )

    edge_contacts = {"PContact", "NContact"}
    edge_coils = {"PCoil", "NCoil"}
    for el in doc.iter():
        tag = etree.QName(el.tag).localname
        if tag == "contact" and el.get("typeContact") in edge_contacts:
            var = el.get("contactVariableName")
            kind = el.get("typeContact")
        elif tag == "coil" and el.get("typeCoil") in edge_coils:
            var = el.get("coilVariableName")
            kind = el.get("typeCoil")
        else:
            continue
        if var and var in declared and declared[var].upper() != "EBOOL":
            errors.append(
                f"line {el.sourceline}: {kind} on '{var}' requires an EBOOL "
                f"variable (edge memory), but it is declared {declared[var]}."
            )
    return errors
