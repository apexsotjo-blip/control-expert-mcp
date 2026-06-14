"""BIG TEST phase 0: inventory the reference project using only MCP read
tools. Output goes to bigtest/ref/. The reference is NEVER saved."""

import json
import os
import sys

sys.path.insert(0, r"..\..\src")

from control_expert_mcp.bridge import ControlExpertBridge, CEError  # noqa: E402

REF = r"C:\Users\TOJG\Desktop\backups\PLC\sw25-gma-01.plc_20260608_2200_fat.stu"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ref")
os.makedirs(OUT, exist_ok=True)

b = ControlExpertBridge()
weaknesses: list[str] = []


def save(name, data):
    path = os.path.join(OUT, name)
    if isinstance(data, (dict, list)):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, default=str)
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(data or "")
    return path


print("open:", b.open_project(REF).get("project", {}).get("name"))

print("structure...")
struct = b.get_project_structure()
save("structure.json", struct)
for t in struct["tasks"]:
    print(f"  task {t['name']}: {len(t['sections'])} sections")

print("variables...")
v = b.list_variables(None, 2000)
save("variables.json", v)
print(f"  {v['total_variables']} vars")

print("data types...")
dt = b.list_data_types()
save("datatypes.json", dt)
print(f"  {len(dt.get('dfbs', []))} DFBs, {len(dt.get('ddts', []))} DDTs")

print("DDT/DFB exports...")
os.makedirs(os.path.join(OUT, "types"), exist_ok=True)
for kind, items in (("ddt", dt.get("ddts", [])), ("dfb", dt.get("dfbs", []))):
    for it in items:
        name = it["name"]
        try:
            r = b.export_xml(kind, None, name)
            xml = r.get("xml") or (open(r["file"], encoding="utf-8").read() if r.get("file") else "")
            save(os.path.join("types", f"{kind}_{name}.xml"), xml)
        except CEError as e:
            msg = f"export_xml({kind}, {name}) failed: {str(e)[:160]}"
            print("  WEAK:", msg)
            weaknesses.append(msg)

print("sections...")
os.makedirs(os.path.join(OUT, "sections"), exist_ok=True)
for t in struct["tasks"]:
    for s in t["sections"]:
        try:
            r = b.read_section(t["name"], s["name"])
            save(os.path.join("sections", f"{t['name']}__{s['name']}.xml"), r["xml"])
        except CEError as e:
            msg = f"read_section({t['name']}, {s['name']}) failed: {str(e)[:160]}"
            print("  WEAK:", msg)
            weaknesses.append(msg)

print("hardware...")
save("hardware.json", b.get_hardware())

print("DTMs...")
dtms = b.list_dtms()
save("dtms.json", dtms)

try:
    md = b.get_master_dtm_dataset(None)
    xml = md.get("xml") or (open(md["file"], encoding="utf-8").read() if md.get("file") else "")
    save("master_dataset.xml", xml)
except CEError as e:
    weaknesses.append(f"get_master_dtm_dataset failed: {str(e)[:160]}")


def walk_dtms(items, depth=0):
    for d in items or []:
        yield d, depth
        yield from walk_dtms(d.get("children"), depth + 1)


os.makedirs(os.path.join(OUT, "dtm_datasets"), exist_ok=True)
for d, depth in walk_dtms(dtms.get("dtms")):
    name = d.get("alias") or d.get("name")
    if depth == 0:
        try:
            r = b.get_dtm_control_parameters(name)
            save("master_control_params.xml", r.get("xml") or "")
        except CEError as e:
            weaknesses.append(f"get_dtm_control_parameters({name}): {str(e)[:140]}")
        continue
    try:
        r = b.get_dtm_dataset(name)
        xml = r.get("xml") or ""
        if r.get("file"):
            with open(r["file"], encoding="utf-8", errors="replace") as f:
                xml = f.read()
        save(os.path.join("dtm_datasets", f"{name}.xml"), xml)
    except CEError as e:
        weaknesses.append(f"get_dtm_dataset({name}): {str(e)[:140]}")

print("animation tables...")
at = b.list_animation_tables()
save("anim_tables.json", at)

save("weaknesses_inventory.json", weaknesses)
print(f"\n{len(weaknesses)} weaknesses logged")
b.close_project(False)
print("INVENTORY DONE ->", OUT)
