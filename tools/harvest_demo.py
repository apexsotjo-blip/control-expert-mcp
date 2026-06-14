"""Harvest reference section XML per language from the CE demo project."""

import json
import os
import sys

sys.path.insert(0, r"..\src")

from control_expert_mcp.bridge import ControlExpertBridge  # noqa: E402

OUT = os.path.abspath("lang_refs")
os.makedirs(OUT, exist_ok=True)

b = ControlExpertBridge()
print("opening demo project (can take a couple of minutes)...")
r = b.open_project(r"C:\Users\Public\Documents\Schneider Electric\Control Expert 14.0\demo_ControlExpert_M340.stu")
print("opened:", json.dumps(r["project"], default=str)[:300])

struct = b.get_project_structure()
print(json.dumps(struct, indent=1))

per_lang: dict[str, list[str]] = {}
for task in struct["tasks"]:
    for sec in task.get("sections", []):
        per_lang.setdefault(sec["language"], []).append((task["name"], sec["name"]))

for lang, secs in per_lang.items():
    for i, (task, name) in enumerate(secs[:3]):
        try:
            xml = b.read_section(task, name)["xml"]
            fn = os.path.join(OUT, f"{lang}_{i}_{name[:24]}.xml".replace(" ", "_"))
            with open(fn, "w", encoding="utf-8") as f:
                f.write(xml)
            print(f"saved {lang}: {name} -> {fn} ({len(xml)} chars)")
        except Exception as e:
            print(f"FAIL {lang} {name}: {str(e)[:120]}")

b.close_project(False)
print("HARVEST DONE")
