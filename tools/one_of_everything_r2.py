"""Round 2: fix the diagnosed issues on GMA_Recreate and commission on the sim."""

import json
import sys

sys.path.insert(0, r"..\src")

from control_expert_mcp.bridge import ControlExpertBridge, CEError  # noqa: E402
from control_expert_mcp.lang_reference import REFERENCES  # noqa: E402

b = ControlExpertBridge()
report = []


def step(label, fn):
    try:
        r = fn()
        msg = f"OK   {label}"
        if isinstance(r, dict):
            msg += f" -> {json.dumps(r, default=str)[:170]}"
        print(msg)
        report.append(msg)
        return r
    except CEError as e:
        msg = f"FAIL {label}: {str(e)[:250]}"
        print(msg)
        report.append(msg)
        return None


step("open", lambda: {"p": b.open_project(r"C:\Users\TOJG\Desktop\MCP server\demo-projects\GMA_Recreate.stu")["project"]["name"]})

# power supply on the remote rack — PSU slot is topo -1 ('(P)')
step("PSU on EIO rack", lambda: b.add_io_module("BMX CPS 2010", -1, "01.00", 0, 1, "EIO"))

step("LD section", lambda: b.import_xml(REFERENCES["LD"]["example"], None, "section", "MAST", "overwrite"))
step("FBD section", lambda: b.import_xml(REFERENCES["FBD"]["example"], None, "section", "MAST", "overwrite"))
step("SFC section", lambda: b.import_xml(REFERENCES["SFC"]["example"], None, "section", "MAST", "overwrite"))

r = step("build", lambda: b.build_project(True))
if r and r.get("build_state") != "built_ok":
    print((r.get("output") or "")[-1200:])
step("save", lambda: b.save_project(None))

print("\n=== simulator ===")
step("setup sim", lambda: b.plc_setup_connection("simulator", "127.0.0.1", None))
step("connect sim", lambda: b.plc_connect("simulator", "primary"))
st = step("state", lambda: b.plc_state())
if st and st.get("connected"):
    step("download", lambda: b.plc_transfer("pc_to_plc"))
    step("RUN", lambda: b.plc_command("run"))
    step("final state", lambda: b.plc_state())
    step("disconnect", lambda: b.plc_disconnect())

b.close_project(False)
print("\n=== SUMMARY ===")
print(f"{sum(1 for x in report if x.startswith('OK'))}/{len(report)} OK")
for line in report:
    if line.startswith("FAIL"):
        print(line)
print("ROUND2 DONE")
