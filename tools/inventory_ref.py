"""Inventory the reference project for the from-scratch recreation test."""

import json
import sys

sys.path.insert(0, r"..\src")

import win32com.client  # noqa: E402

from control_expert_mcp.bridge import ControlExpertBridge  # noqa: E402

REF = r"C:\Users\TOJG\Desktop\backups\PLC\sw25-gma-01.plc_20260608_2200_fat.stu"

b = ControlExpertBridge()
print("opening reference (may take minutes)...")
r = b.open_project(REF)
print("project:", json.dumps(r["project"], default=str))

print("\n--- structure ---")
print(json.dumps(b.get_project_structure(), default=str, indent=1)[:3000])

print("\n--- hardware ---")
try:
    print(json.dumps(b.get_hardware(), default=str, indent=1)[:2500])
except Exception as e:
    print("hw fail:", str(e)[:150])

print("\n--- data types ---")
try:
    print(json.dumps(b.list_data_types(), default=str)[:1500])
except Exception as e:
    print("types fail:", str(e)[:150])

print("\n--- variables (sample + stats) ---")
v = b.list_variables(None, 30)
print("total:", v["total_variables"])
from collections import Counter  # noqa: E402

types = Counter(e.get("type", "?") for e in v["variables"])
print("first-30 type mix:", dict(types))
for e in v["variables"][:12]:
    print(" ", e)

print("\n--- animation tables / dtms / networks ---")


def probe():
    proj = b._project(write=False)
    try:
        at = b._wrap(b._get_prop(proj, "AnimationTables"))
        n = at.Count
        if callable(n):
            n = n()
        print("animation tables:", int(n))
        for i, t in enumerate(at):
            td = win32com.client.Dispatch(t)
            print("  table:", td.Name)
            if i >= 9:
                break
    except Exception as e:
        print("anim tables fail:", str(e)[:140])
    try:
        print("networks:", json.dumps(b._do_list_networks(), default=str)[:400])
    except Exception as e:
        print("networks fail:", str(e)[:120])


b._run(probe)
try:
    print("dtms:", json.dumps(b.list_dtms(), default=str)[:600])
except Exception as e:
    print("dtms fail:", str(e)[:140])

b.close_project(False)
print("INVENTORY DONE")
