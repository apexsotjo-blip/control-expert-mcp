"""Phase 2: user DDTs and DFBs, regenerated from the inventoried definitions.

Auto-generated device DDTs (T_*) are skipped — hardware/DTM creation makes
them. Imports run in passes so intra-type dependencies resolve themselves.
"""

import os
import re

from common import ControlExpertBridge, REF_DIR, WORK, kill_strays, log_weak, ref_json

kill_strays()
b = ControlExpertBridge()
b.open_project(WORK)

ref_types = ref_json("datatypes.json")
have = b.list_data_types()
have_ddts = {d["name"] for d in have.get("ddts", [])}
have_dfbs = {d["name"] for d in have.get("dfbs", [])}
print(f"already present: {len(have_ddts)} DDTs, {len(have_dfbs)} DFBs")

OUR_HEADER = (
    '<fileHeader company="Schneider Automation" product="Control Expert V14.0 - 190112" '
    'dateTime="date_and_time#2026-6-11-23:0:0" content="{content}" DTDVersion="41"></fileHeader>\n'
    '\t<contentHeader name="DR001 Dabouq Reservoir" version="0.0.1" '
    'dateTime="date_and_time#2026-6-11-23:0:0"></contentHeader>'
)


def regenerate(xml: str, content: str) -> str:
    """Re-emit an exchange document under our own headers."""
    xml = re.sub(r"<fileHeader\b[^>]*>\s*</fileHeader>\s*<contentHeader\b[^>]*>\s*</contentHeader>",
                 OUR_HEADER.format(content=content), xml, count=1, flags=re.S)
    return xml


def import_passes(kind: str, names: list[str], content: str) -> None:
    pending = list(names)
    for round_no in range(1, 6):
        failed = []
        for name in pending:
            path = os.path.join(REF_DIR, "types", f"{kind}_{name}.xml")
            xml = regenerate(open(path, encoding="utf-8").read(), content)
            try:
                b.import_xml(xml, None, kind, None, "overwrite")
                print(f"OK   {kind} {name}")
            except Exception as e:  # noqa: BLE001
                failed.append((name, str(e)[:160]))
        if not failed:
            return
        if len(failed) == len(pending):
            for name, err in failed:
                log_weak(f"import {kind} {name}: {err}")
            return
        pending = [n for n, _ in failed]
        print(f" pass {round_no}: {len(pending)} pending")


ddt_todo = [d["name"] for d in ref_types.get("ddts", [])
            if not d["name"].startswith("T_") and d["name"] not in have_ddts]
print(f"user DDTs to create: {len(ddt_todo)}: {ddt_todo}")
import_passes("ddt", ddt_todo, "Derived Data Type source file")

dfb_todo = [d["name"] for d in ref_types.get("dfbs", []) if d["name"] not in have_dfbs]
print(f"DFBs to create: {len(dfb_todo)}")
import_passes("dfb", dfb_todo, "Function Block source file")

after = b.list_data_types()
print(f"now: {len(after.get('ddts', []))} DDTs, {len(after.get('dfbs', []))} DFBs "
      f"(ref: {len(ref_types.get('ddts', []))}/{len(ref_types.get('dfbs', []))})")
b.save_project(None)
b.close_project(False)
print("PHASE2 DONE")
