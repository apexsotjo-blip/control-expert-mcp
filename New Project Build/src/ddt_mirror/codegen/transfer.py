"""'Transfer to RemoteConnect': everything the engineer needs to move the
project from the Control Expert authoring world into a SCADAPack RTU.

One call produces, in the chosen folder:
1. `<stem>_RTU_import.xls`  - the engineer's exported RTU config plus one
   object per selected leaf (codegen.remoteconnect round-trip).
2. `<stem>_RTU_mirror.st`   - paste-ready Logic Editor mirror section
   (Tag.Member <-> Object.value copies).
3. `<stem>_RTU_point_map.csv` - object/point/register map including the
   HMI-side IEC address (0- or 1-indexed per settings.hmi_index_base).
4. `<stem>_RTU_variables.xsy` - the project's variables export WITHOUT the
   generated located mirror variables (they are M580 plumbing; importing
   them into the Logic Editor would only pollute it) - import via the
   Logic Editor's variables import.
5. `<stem>_RTU_sections/NN_<task>_<section>.<ext>` - every logic section
   as its own exchange file, typed by language (.xst/.xld/...), EXCLUDING
   the generated HMI_MIRROR section (replaced by the .st above) - import
   one by one in the Logic Editor.

Needs the Control Expert bridge (COM): run it on the worker thread.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from lxml import etree

from ..core.engine import ProjectData
from ..core.persist import SidecarState
from ..core.xsy_parser import fetch_variables_xml
from .remoteconnect import RcReport, export_remoteconnect

MIRROR_COMMENT_PREFIX = "HMI mirror of "

# CE exchange-file extensions by section language (RC Logic Editor import
# filters by these; the XML grammar is the same PGMExchangeFile either way)
SECTION_EXT = {
    "ST": ".xst",
    "LD": ".xld",
    "FBD": ".xbd",
    "IL": ".xil",
    "SFC": ".xsf",
}


def filter_xsy(xml_text: str) -> tuple[str, list[str]]:
    """Drop the generated located mirror variables from a variables export.

    Mirror variables are identified by their generated comment marker
    ('HMI mirror of <path>'), not by name prefix - the prefix is
    user-configurable and may be empty. Returns (filtered xml, removed
    names)."""
    root = etree.fromstring(xml_text.encode("utf-8"))
    removed: list[str] = []
    for block in root.findall("dataBlock"):
        for var in list(block.findall("variables")):
            comment = var.find("comment")
            text = (comment.text or "") if comment is not None else ""
            if text.startswith(MIRROR_COMMENT_PREFIX):
                removed.append(var.get("name", "?"))
                block.remove(var)
    out = etree.tostring(root, xml_declaration=True, encoding="UTF-8",
                         standalone=True)
    return out.decode("utf-8"), removed


def _safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)


@dataclass
class TransferReport:
    ok: bool = False
    rc: RcReport = field(default_factory=RcReport)
    xsy_path: str = ""
    xsy_removed: int = 0
    sections_dir: str = ""
    section_files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def export_variables_xsy(bridge, out_path: str) -> tuple[int, list[str]]:
    """Write the project's variables export minus the mirror variables."""
    filtered, removed = filter_xsy(fetch_variables_xml(bridge))
    warnings: list[str] = []
    if re.search(r'topologicalAddress="%', filtered):
        warnings.append(
            "the variables export still contains located (%...) tags that "
            "were located in the source project - verify the Logic Editor "
            "accepts them or unlocate them there")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(filtered)
    return len(removed), warnings


def export_sections(bridge, exclude_section: str, out_dir: str,
                    ) -> tuple[list[str], list[str]]:
    """One exchange file per logic section, in task/section order, typed by
    language; `exclude_section` (the generated mirror) is skipped."""
    files: list[str] = []
    warnings: list[str] = []
    structure = bridge.get_project_structure()
    n = 0
    for task in structure.get("tasks", []):
        for sec in task.get("sections", []):
            name, language = sec["name"], sec.get("language", "?")
            if name.lower() == exclude_section.lower():
                continue
            n += 1
            ext = SECTION_EXT.get(language.upper())
            if ext is None:
                warnings.append(
                    f"section '{name}' ({language}): unknown exchange "
                    "extension - written as .xpg.xml")
                ext = ".xpg.xml"
            result = bridge.read_section(task["name"], name)
            fname = _safe_filename(f"{n:02d}_{task['name']}_{name}{ext}")
            path = os.path.join(out_dir, fname)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(result["xml"])
            files.append(path)
    return files, warnings


def transfer_to_remoteconnect(
    bridge,
    data: ProjectData,
    state: SidecarState,
    src_xls: str,
    out_dir: str,
    project_path: str,
    timestamp: str = "",
    progress=lambda msg: None,
) -> TransferReport:
    report = TransferReport()

    progress("Generating RTU objects workbook + mirror ST...")
    report.rc = export_remoteconnect(
        data, state, src_xls, out_dir, project_path, timestamp)
    report.warnings.extend(report.rc.warnings)

    stem = os.path.splitext(os.path.basename(project_path))[0] or "ddt_mirror"

    progress("Exporting variables (.xsy) without mirror tags...")
    report.xsy_path = os.path.join(out_dir, f"{stem}_RTU_variables.xsy")
    report.xsy_removed, xsy_warnings = export_variables_xsy(
        bridge, report.xsy_path)
    report.warnings.extend(xsy_warnings)

    progress("Exporting logic sections...")
    report.sections_dir = os.path.join(out_dir, f"{stem}_RTU_sections")
    os.makedirs(report.sections_dir, exist_ok=True)
    report.section_files, sec_warnings = export_sections(
        bridge, state.settings.section_name, report.sections_dir)
    report.warnings.extend(sec_warnings)

    report.ok = True
    progress("Transfer bundle complete.")
    return report
