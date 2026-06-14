"""Connect/download/run against the ALREADY RUNNING simulator."""

import json
import sys
import time

sys.path.insert(0, r"..\src")

from control_expert_mcp.bridge import ControlExpertBridge, CEError  # noqa: E402

b = ControlExpertBridge()
b.open_project(r"C:\Users\TOJG\Desktop\MCP server\demo-projects\GMA_Sim.stu")
print("build:", b.build_project(False).get("build_state"))
print("setup:", b.plc_setup_connection("simulator", "127.0.0.1", None))
print("connect:", b.plc_connect("simulator", "primary"))
st = b.plc_state()
print("state:", st)
if st.get("connected"):
    for label, fn in (
        ("download", lambda: b.plc_transfer("pc_to_plc")),
        ("run", lambda: b.plc_command("run")),
        ("final", lambda: b.plc_state()),
        ("disconnect", lambda: b.plc_disconnect()),
    ):
        try:
            print(f"{label}:", json.dumps(fn(), default=str)[:220])
            time.sleep(2)
        except CEError as e:
            print(f"{label} FAIL:", str(e)[:250])
b.close_project(False)
