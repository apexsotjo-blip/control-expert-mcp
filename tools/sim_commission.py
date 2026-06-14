"""Clean local-only project -> built_ok -> launch simulator -> download -> RUN."""

import json
import subprocess
import sys
import time

sys.path.insert(0, r"..\src")

from control_expert_mcp.bridge import ControlExpertBridge, CEError  # noqa: E402
from control_expert_mcp.lang_reference import REFERENCES  # noqa: E402

SIM_EXE = r"C:\Program Files (x86)\Schneider Electric\Control Expert 14.0\PLC_Simulator\sim.exe"

b = ControlExpertBridge()
rep = []


def step(label, fn):
    try:
        r = fn()
        m = f"OK   {label}" + (f" -> {json.dumps(r, default=str)[:150]}" if isinstance(r, dict) else "")
        print(m)
        rep.append(True)
        return r
    except CEError as e:
        print(f"FAIL {label}: {str(e)[:200]}")
        rep.append(False)
        return None


# Launch the simulator first (it sits in the tray; connect needs it running).
# Launching while one is already running pops 'Can't register Simulator' —
# check first.
already = "sim.exe" in subprocess.run(
    ["tasklist", "/FI", "IMAGENAME eq sim.exe", "/FO", "CSV", "/NH"],
    capture_output=True, text=True).stdout.lower()
if already:
    print("sim.exe already running")
else:
    try:
        subprocess.Popen([SIM_EXE])
        print("launched sim.exe; waiting for it to come up...")
        time.sleep(12)
    except Exception as e:
        print("sim launch:", str(e)[:120])

step("new local M340", lambda: {"cpu": b.new_project("BMX P34 2020", "02.70", "GMA_Sim")["project"]["cpu"]})
step("AMI 0810 @1", lambda: b.add_io_module("BMX AMI 0810", 1, "01.00", 0, None, None))
step("DDI 1602 @2", lambda: b.add_io_module("BMX DDI 1602", 2, "02.00", 0, None, None))
step("DDO 1602 @3", lambda: b.add_io_module("BMX DDO 1602", 3, "02.00", 0, None, None))

for name, typ in (("Pump1", "BOOL"), ("Speed", "REAL"), ("RawAI", "INT"),
                  ("StartPB", "BOOL"), ("StopPB", "BOOL"), ("Motor", "BOOL"),
                  ("IdleLamp", "BOOL"), ("RunLamp", "BOOL"), ("PumpReady", "BOOL"),
                  ("FaultActive", "BOOL"), ("PumpRun", "BOOL"),
                  ("SeqStart", "BOOL"), ("FillDone", "BOOL"), ("DrainDone", "BOOL")):
    b.create_variable(name, typ, None, None, None)

step("ST", lambda: b.write_st_logic("MAST", "Supervisor",
     "Motor := (StartPB OR Motor) AND NOT StopPB;\nRunLamp := Motor;\nIF Motor THEN Speed := 100.0; ELSE Speed := 0.0; END_IF;\n", None))
step("LD", lambda: b.import_xml(REFERENCES["LD"]["example"], None, "section", "MAST", "overwrite"))
step("FBD", lambda: b.import_xml(REFERENCES["FBD"]["example"], None, "section", "MAST", "overwrite"))
step("SFC", lambda: b.import_xml(REFERENCES["SFC"]["example"], None, "section", "MAST", "overwrite"))
step("anim table", lambda: b.create_animation_table(
    "AT_Sim", ["StartPB", "StopPB", "Motor", "RunLamp", "Speed", "RawAI",
               "SeqStart", "FillDone", "DrainDone", "PumpRun"]))

r = step("build", lambda: b.build_project(True))
if r and r.get("build_state") != "built_ok":
    print((r.get("output") or "")[-700:])
step("save", lambda: b.save_project(r"C:\Users\TOJG\Desktop\MCP server\demo-projects\GMA_Sim.stu"))

if r and r.get("build_state") == "built_ok":
    print("\n=== simulator commissioning ===")
    step("setup sim", lambda: b.plc_setup_connection("simulator", "127.0.0.1", None))
    step("connect", lambda: b.plc_connect("simulator", "primary"))
    st = step("state after connect", lambda: b.plc_state())
    if st and st.get("connected"):
        if st.get("plc_state") == "run":
            step("STOP before download", lambda: b.plc_command("stop"))
        step("download", lambda: b.plc_transfer("pc_to_plc"))
        time.sleep(3)
        step("RUN", lambda: b.plc_command("run"))
        time.sleep(2)
        step("FINAL state", lambda: b.plc_state())
        step("disconnect", lambda: b.plc_disconnect())
    else:
        print(">> simulator did not connect; is sim.exe running and selected?")

b.close_project(False)
print(f"\n{sum(rep)}/{len(rep)} steps OK")
print("SIM COMMISSION DONE")
