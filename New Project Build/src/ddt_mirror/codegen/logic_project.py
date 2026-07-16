"""SHELVED — not wired into the app (2026-07-16): the feature was removed
from the GUI/transfer flow until it is production-ready. The module and its
tests are kept because the mechanism below (OEM broker context via the x70
DTM's xpdf resource, plus bridge.new_logic_editor_project in the parent
repo) was probe-verified end-to-end and is expensive to rediscover. To
revive: re-add a generate_logic_stu flag to transfer_to_remoteconnect and a
checkbox on the RTU page. NOTE before reviving: fix the open-project state
bug — generating the .stu closes the CE project and leaves the Logic Editor
project open in the bridge, so the GUI must reopen the original project (or
block further actions) afterwards.

Generate a native RemoteConnect Logic Editor project (.stu) directly,
as an alternative to the per-file import bundle.

The Logic Editor is an OEM Control Expert served by the shared broker; it
is selected by handing the broker a context whose XpdfContext is the
SCADAPack x70 DTM's embedded xpdf resource. We drive it exactly like a CE
project: create the x70 project, import the engineer's variables (.xsy)
and logic sections, add the mirror ST section, and save.

The produced .stu is NATIVE to the installed Logic Editor product (same
version and STU-compatibility level), so that product opens it directly -
unlike a Control-Expert-authored x70 file, which is a newer file level the
Logic Editor would reject. The mirror section references the RTU object
variables (T_SPx70_* structs), which do not exist until RemoteConnect's
object-database sync creates them, so the project is saved UNBUILT; it
builds after the engineer syncs and imports the objects .xls.

Requires the SCADAPack x70 DTM (RemoteConnect) installed - it supplies the
xpdf routing resource. Runs COM: call from the worker thread.
"""

from __future__ import annotations

import glob
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field

_DTM_GLOBS = [
    r"C:\Program Files (x86)\Common Files\FDT\DTMs\Schneider Electric"
    r"\SCADAPack x70\Schneider_Electric.SCADAPackRTUDeviceDtm.Dtm.dll",
    r"C:\Program Files\Common Files\FDT\DTMs\Schneider Electric"
    r"\SCADAPack x70\Schneider_Electric.SCADAPackRTUDeviceDtm.Dtm.dll",
]

# Embedded-resource name fragments inside that assembly.
_XPDF_RES = "UnityProXpdfResource.xml"
_XSO_RES = "UnityProDefaultProjectSettings.xso"

_cache: dict[str, str] = {}


def find_dtm() -> str | None:
    for pattern in _DTM_GLOBS:
        for hit in glob.glob(pattern):
            return hit
    # version-agnostic fallback
    for root in (r"C:\Program Files (x86)\Common Files\FDT\DTMs",
                 r"C:\Program Files\Common Files\FDT\DTMs"):
        for hit in glob.glob(os.path.join(
                root, "**", "*SCADAPackRTUDeviceDtm.Dtm.dll"), recursive=True):
            return hit
    return None


_PS_EXTRACT = r"""
$ErrorActionPreference = 'Stop'
$dtm = '{dtm}'
$dir = Split-Path $dtm
$null = [AppDomain]::CurrentDomain.add_AssemblyResolve({{
    param($s, $e)
    $name = ($e.Name -split ',')[0]
    $cand = Join-Path $dir "$name.dll"
    if (Test-Path $cand) {{ return [Reflection.Assembly]::LoadFile($cand) }}
    return $null
}})
$asm = [Reflection.Assembly]::LoadFile($dtm)
function Dump($fragment, $out) {{
    $n = $asm.GetManifestResourceNames() | Where-Object {{ $_ -match $fragment }} | Select-Object -First 1
    if (-not $n) {{ throw "resource $fragment not found" }}
    $s = $asm.GetManifestResourceStream($n)
    $r = New-Object System.IO.StreamReader($s)
    [System.IO.File]::WriteAllText($out, $r.ReadToEnd())
}}
Dump 'Xpdf' '{xpdf_out}'
Dump 'DefaultProjectSettings' '{xso_out}'
Write-Output 'OK'
"""


def extract_dtm_resources() -> tuple[str, str]:
    """Return (xpdf_context_text, settings_xso_text) from the installed x70
    DTM. Cached for the process. Raises RuntimeError if the DTM is missing
    or reflection fails."""
    if "xpdf" in _cache and "xso" in _cache:
        return _cache["xpdf"], _cache["xso"]
    dtm = find_dtm()
    if not dtm:
        raise RuntimeError(
            "The SCADAPack x70 device DTM was not found. Install "
            "RemoteConnect (with the SCADAPack x70 DTM) to generate a Logic "
            "Editor project; the per-file import bundle needs no install.")
    tmp = tempfile.mkdtemp(prefix="ddtmirror_dtm_")
    xpdf_out = os.path.join(tmp, "xpdf.xml")
    xso_out = os.path.join(tmp, "settings.xso")
    script = _PS_EXTRACT.format(
        dtm=dtm.replace("'", "''"),
        xpdf_out=xpdf_out.replace("\\", "\\\\"),
        xso_out=xso_out.replace("\\", "\\\\"))
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True)
    if proc.returncode != 0 or not os.path.exists(xpdf_out):
        raise RuntimeError(
            "Could not extract the Logic Editor routing resource from the "
            f"x70 DTM ({dtm}). {proc.stderr.strip()[:400]}")
    with open(xpdf_out, encoding="utf-8-sig") as fh:
        _cache["xpdf"] = fh.read()
    with open(xso_out, encoding="utf-8-sig") as fh:
        _cache["xso"] = fh.read()
    for f in (xpdf_out, xso_out):
        try:
            os.remove(f)
        except OSError:
            pass
    try:
        os.rmdir(tmp)
    except OSError:
        pass
    return _cache["xpdf"], _cache["xso"]


# device-type label (RC 'RtuProjectSettingsDeviceType') -> catalog family
_PLATFORMS = ("SCADAPack47x", "SCADAPack57x", "SCADAPack1070")


def device_type_to_platform(device_type: str) -> str:
    """Map an RC device-type label to a Logic Editor catalog family.

    Labels look like 'SCADAPack 474 <61>' / 'SCADAPack 535E <..>' /
    'SCADAPack 100 <..>'. Family by model number: 4xx -> 47x, 5xx -> 57x,
    1xx/10xx -> 1070. Defaults to 57x when nothing matches."""
    m = re.search(r"(\d{3,4})", device_type or "")
    if not m:
        return "SCADAPack57x"
    n = m.group(1)
    if n.startswith("1"):
        return "SCADAPack1070"
    if n.startswith("4"):
        return "SCADAPack47x"
    if n.startswith("5"):
        return "SCADAPack57x"
    return "SCADAPack57x"


@dataclass
class LogicProjectReport:
    ok: bool = False
    stu_path: str = ""
    platform: str = ""
    imported_sections: list[str] = field(default_factory=list)
    imported_variables: bool = False
    mirror_section: str = ""
    warnings: list[str] = field(default_factory=list)


def _section_task(xml_text: str, default: str = "MAST") -> str:
    m = re.search(r'<identProgram[^>]*task="([^"]+)"', xml_text)
    return m.group(1) if m else default


def generate_logic_project(
    bridge,
    platform: str,
    xsy_path: str,
    section_files: list[str],
    mirror_st_text: str,
    mirror_section_name: str,
    out_stu: str,
    progress=lambda msg: None,
) -> LogicProjectReport:
    """Build a native Logic Editor .stu from an already-produced transfer
    bundle (its filtered .xsy, per-section exchange files and mirror ST).

    Per-item import failures are collected as warnings rather than aborting,
    so a partial project still saves - the engineer sees exactly what did
    not transfer."""
    report = LogicProjectReport(platform=platform,
                                mirror_section=mirror_section_name)

    progress(f"Starting the Logic Editor ({platform})...")
    xpdf, xso = extract_dtm_resources()
    bridge.new_logic_editor_project(platform, xpdf, xso)

    if xsy_path and os.path.isfile(xsy_path):
        progress("Importing variables...")
        try:
            bridge.import_xml(None, xsy_path, "variables", None, "overwrite")
            report.imported_variables = True
        except Exception as exc:
            report.warnings.append(f"variable import failed: {exc}")

    for path in section_files:
        name = os.path.basename(path)
        progress(f"Importing section {name}...")
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                task = _section_task(fh.read())
        except OSError:
            task = "MAST"
        try:
            bridge.import_xml(None, path, "section", task, "overwrite")
            report.imported_sections.append(name)
        except Exception as exc:
            report.warnings.append(f"section '{name}' import failed: {exc}")

    if mirror_st_text:
        progress("Adding the mirror ST section...")
        try:
            bridge.write_st_logic("MAST", mirror_section_name,
                                  mirror_st_text, None)
        except Exception as exc:
            report.warnings.append(f"mirror section failed: {exc}")

    progress("Saving the Logic Editor project (.stu)...")
    result = bridge.save_project(out_stu)
    report.stu_path = result.get("saved_as", out_stu)
    report.ok = True
    report.warnings.append(
        "Saved UNBUILT: the mirror section references RTU object variables "
        "(T_SPx70_*) that RemoteConnect creates during object-database sync. "
        "Open the .stu in the Logic Editor, sync/import the objects .xls, "
        "then build.")
    progress("Logic Editor project complete.")
    return report
