"""Phase 7: delete the duplicate (_0) device DDTs left by ZEF reimports, final
build, and tally the honest error categories."""

import re

from common import ControlExpertBridge, WORK, kill_strays, step

kill_strays()
b = ControlExpertBridge()
b.open_project(WORK)

# remove _0 duplicate device DDTs
dupes = [v["name"] for v in b.list_variables("_0", 300)["variables"]
         if re.search(r"_\d+$", v["name"]) and v["name"].startswith("EIO2")]
print("duplicate device DDTs to delete:", dupes)
for name in dupes:
    step(f"delete {name}", lambda name=name: b.delete_variable(name), weak_on_fail=False)

b.save_project(WORK)
r = b.build_project(True)
out = r.get("output") or ""

cats = {
    "E1203 multi-assign": len(re.findall(r"E1203", out)),
    "device DDT not mapped": len(re.findall(r"not mapped on a device", out)),
    "HART AHI undefined (BME_AHI)": len(re.findall(r"BME_AHI_0812", out)),
    "EIP HSBY": len(re.findall(r"EIP option must be checked", out)),
    "power supply": len(re.findall(r"power|voltage", out, re.I)),
}
m = re.search(r"Process (succeeded|failed)\s*:\s*(\d+)\s*Error", out)
print("\nbuild_state:", r.get("build_state"))
print("totals:", m.group(0) if m else "n/a")
for k, v in cats.items():
    print(f"  {k}: {v}")
b.save_project(WORK)
b.close_project(False)
print("PHASE7 DONE")
