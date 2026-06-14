"""Phase 5: program sections in reference order. ST goes through the plain
write_st_logic authoring path; FBD through regenerated section XML."""

import html
import re

from common import ControlExpertBridge, WORK, kill_strays, log_weak, ref_json, ref_text

kill_strays()
b = ControlExpertBridge()
b.open_project(WORK)

OUR_HEADER = (
    '<fileHeader company="Schneider Automation" product="Control Expert V14.0 - 190112" '
    'dateTime="date_and_time#2026-6-11-23:0:0" content="{content}" DTDVersion="41"></fileHeader>\n'
    '\t<contentHeader name="DR001 Dabouq Reservoir" version="0.0.1" '
    'dateTime="date_and_time#2026-6-11-23:0:0"></contentHeader>'
)

struct = ref_json("structure.json")
existing = {s["name"] for t in b.get_project_structure()["tasks"] for s in t["sections"]}

for t in struct["tasks"]:
    for s in t["sections"]:
        name, lang = s["name"], s["language"]
        if name in existing:
            print(f"skip {name} (exists)")
            continue
        xml = ref_text("sections", f"{t['name']}__{name}.xml")
        try:
            if lang == "ST":
                src = re.search(r"<STSource>(.*?)</STSource>", xml, re.S).group(1)
                src = html.unescape(src)
                b.write_st_logic(t["name"], name, src, None)
            else:
                regen = re.sub(
                    r"<fileHeader\b[^>]*>\s*</fileHeader>\s*<contentHeader\b[^>]*>\s*</contentHeader>",
                    OUR_HEADER.format(content="Program source file"), xml, count=1, flags=re.S)
                b.import_xml(regen, None, "section", t["name"], "overwrite")
            print(f"OK   {lang:3s} {name}")
        except Exception as e:  # noqa: BLE001
            log_weak(f"section {name} ({lang}): {str(e)[:200]}")

now = b.get_project_structure()
got = [s["name"] for tk in now["tasks"] for s in tk["sections"]]
want = [s["name"] for tk in struct["tasks"] for s in tk["sections"]]
print(f"sections now: {len(got)}/{len(want)}; order matches ref: {got == want}")
b.save_project(None)
b.close_project(False)
print("PHASE5 DONE")
