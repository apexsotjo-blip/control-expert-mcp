"""Smoke test: drive the bridge directly (no MCP transport).

1. Create the broker and a new in-memory application
2. Create a project for an M340 CPU
3. Add a variable + an ST section, analyze
4. Report status and close without saving
"""

import json
import sys

sys.path.insert(0, r"..\src")

from control_expert_mcp.bridge import ControlExpertBridge  # noqa: E402


def show(label, value):
    print(f"\n=== {label} ===")
    print(json.dumps(value, indent=2, default=str))


bridge = ControlExpertBridge()

show("status (no app)", bridge.get_status())

cpu = sys.argv[1] if len(sys.argv) > 1 else "BMX P34 2020"
ver = sys.argv[2] if len(sys.argv) > 2 else "02.70"
show("new_project", bridge.new_project(cpu, ver, "McpSmokeTest"))
show("status", bridge.get_status())
show("structure", bridge.get_project_structure())
show("create_variable", bridge.create_variable("MotorRun", "BOOL", "smoke test var", None, None))
show("list_variables", bridge.list_variables("Motor", 10))
show("create_section", bridge.create_section("MAST", "Logic01", "ST"))
show("read_section", bridge.read_section("MAST", "Logic01"))
show("analyze", bridge.analyze_project())
show("close", bridge.close_project(False))
print("\nSMOKE TEST OK")
