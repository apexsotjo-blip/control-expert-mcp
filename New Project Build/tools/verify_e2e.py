"""End-to-end verification of the DDT-mirror engine against Control Expert.

Offline part (default): scratch project + test DDT -> engine generates mirror
variables + HMI_MIRROR section -> build reaches built_ok -> save -> rerun to
prove address stability.

Live part (--live): start the simulator, transfer, run, then prove both copy
directions over Modbus: write the Cmd coil + Man_SP registers, expect the echo
logic's results back in the Running coil + Flow_PV registers.
NOTE: a FRESH simulator needs a one-time manual transfer from the CE GUI to
seed the CPU family ('Family check failed' otherwise).

Usage:  python tools/verify_e2e.py [--live] [--keep-open]
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ddt_mirror.core.engine import apply_plan, build_plan, scan_project
from ddt_mirror.core.persist import SidecarState

CPU_PART = "BMX P34 2020"
CPU_VERSION = "02.70"

WORK_DIR = os.path.join(os.path.dirname(__file__), "..", "_e2e_work")
PROJECT_PATH = os.path.abspath(os.path.join(WORK_DIR, "mirror_e2e.stu"))

DDT_XML = """<?xml version="1.0" encoding="utf-8"?>
<DDTExchangeFile>
\t<fileHeader company="Schneider Automation" product="Control Expert V14.0 - 190112" dateTime="date_and_time#2026-7-14-12:0:0" content="Derived data types source file" DTDVersion="41"></fileHeader>
\t<contentHeader name="Project" version="0.0.1"></contentHeader>
\t<DDTSource DDTName="PumpT">
\t\t<structure>
\t\t\t<variables name="Cmd" typeName="BOOL"></variables>
\t\t\t<variables name="Mode" typeName="INT"></variables>
\t\t\t<variables name="StatusW" typeName="WORD"></variables>
\t\t\t<variables name="Man_SP" typeName="REAL"></variables>
\t\t\t<variables name="Flow_PV" typeName="REAL"></variables>
\t\t\t<variables name="Running" typeName="BOOL"></variables>
\t\t</structure>
\t</DDTSource>
</DDTExchangeFile>
"""

ECHO_ST = """(* test echo logic: makes both mirror directions observable *)
Pump1.Running := Pump1.Cmd;
Pump1.Flow_PV := Pump1.Man_SP * 2.0;
"""


def step(msg: str) -> None:
    print(f"==> {msg}", flush=True)


def check(cond: bool, msg: str) -> None:
    if not cond:
        print(f"FAIL: {msg}", flush=True)
        sys.exit(1)
    print(f"  OK: {msg}", flush=True)


def build_scratch_project(bridge) -> None:
    step(f"Creating scratch project ({CPU_PART} v{CPU_VERSION})")
    bridge.new_project(CPU_PART, CPU_VERSION)

    step("Importing test DDT 'PumpT'")
    bridge.import_xml(DDT_XML, None, "ddt", None, "overwrite")

    step("Creating test tags")
    bridge.create_variable("Pump1", "PumpT", "e2e test pump", None, None)
    bridge.create_variable("Pump2", "PumpT", None, None, None)
    bridge.create_variable("Line_Speed", "INT", "standalone tag", None, None)
    bridge.create_variable("E_Stop", "BOOL", None, None, None)

    step("Writing echo logic section")
    bridge.write_st_logic("MAST", "TEST_LOGIC", ECHO_ST, None)

    os.makedirs(WORK_DIR, exist_ok=True)
    step(f"Saving as {PROJECT_PATH}")
    bridge.save_project(PROJECT_PATH)


def make_state() -> SidecarState:
    state = SidecarState()
    state.selected_types = ["PumpT", "INT", "BOOL"]
    # keep well inside a default M340 memory layout
    state.settings.base_bit = 50
    state.settings.base_word = 200
    return state


def run_engine(bridge, state: SidecarState):
    data = scan_project(bridge)
    plan, alloc = build_plan(data, state, project_name="mirror_e2e")
    report = apply_plan(bridge, plan, alloc, state, PROJECT_PATH, progress=step)
    if not report.ok:
        print(report.build_output)
        print(f"FAIL: apply_plan: {report.error}")
        sys.exit(1)
    return plan, report


def address_map(plan) -> dict[str, tuple[str, str]]:
    """PlcPath -> (Address, Type) from the plan's CSV."""
    rows = csv.DictReader(io.StringIO(plan.csv_text))
    return {r["PlcPath"]: (r["Address"], r["Type"]) for r in rows}


def offline(bridge) -> tuple:
    build_scratch_project(bridge)
    state = make_state()

    step("Engine run #1")
    plan1, report1 = run_engine(bridge, state)
    check(report1.build_state == "built_ok", "project built after generation")
    check(len(report1.created_vars) == 6,
          f"6 mirror variables created: 4 REAL + 2 WORD (got {report1.created_vars})")

    step("Engine run #2 (stability)")
    plan2, report2 = run_engine(bridge, state)
    check(plan1.csv_text == plan2.csv_text, "address map identical across reruns")
    check(report2.created_vars == [] and len(report2.skipped_vars) == 6,
          "rerun created nothing, skipped existing mirrors")

    amap = address_map(plan1)
    check(amap["Pump1.Cmd"][0].startswith("%M") and "W" not in amap["Pump1.Cmd"][0],
          f"BOOL mirrored to coil ({amap['Pump1.Cmd'][0]})")
    check(amap["Line_Speed"][0].startswith("%MW"), "INT mirrored to single %MW")
    check(amap["Pump1.Man_SP"][0].startswith("%MW"), "REAL mirrored to %MW pair")
    for path, (addr, _t) in amap.items():
        if path.endswith(("Man_SP", "Flow_PV")):
            check(int(addr[3:]) % 2 == 0,
                  f"32-bit mirror even-aligned: {path} at {addr}")
    return plan1, state


def live(bridge, plan) -> None:
    from control_expert_mcp.modbus import ModbusClient

    amap = address_map(plan)
    cmd_addr = amap["Pump1.Cmd"][0]
    man_sp_addr = amap["Pump1.Man_SP"][0]
    flow_pv_addr = amap["Pump1.Flow_PV"][0]
    running_addr = amap["Pump1.Running"][0]

    step("Starting simulator")
    bridge.start_simulator(False)
    time.sleep(2)

    step("Connecting + transferring to simulator")
    bridge.plc_setup_connection("simulator", None, None)
    bridge.plc_connect("simulator", "programming")
    if bridge.plc_state().get("plc_state") == "run":
        bridge.plc_command("stop")  # cannot transfer onto a running PLC
    bridge.plc_transfer("pc_to_plc")
    bridge.plc_command("run")

    step("Modbus round-trip via 127.0.0.1:502")
    mb = ModbusClient()
    mb.connect("127.0.0.1", 502, 1, "low_first")
    try:
        mb.write_one(man_sp_addr, "REAL", 21.5)
        mb.write_one(cmd_addr, "BOOL", True)
        time.sleep(1.0)  # a few MAST scans
        flow = mb.read_one(flow_pv_addr, "REAL")
        running = mb.read_one(running_addr, "BOOL")
        check(abs(flow - 43.0) < 1e-3, f"Flow_PV mirror reads 43.0 (got {flow})")
        check(bool(running), f"Running mirror coil is TRUE (got {running})")
    finally:
        mb.disconnect()
        bridge.plc_command("stop")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true",
                    help="also run the simulator + Modbus round-trip")
    ap.add_argument("--keep-open", action="store_true",
                    help="leave the project open in the bridge at the end")
    args = ap.parse_args()

    from control_expert_mcp.bridge import ControlExpertBridge

    bridge = ControlExpertBridge()
    try:
        plan, _state = offline(bridge)
        if args.live:
            live(bridge, plan)
        print("\nE2E PASSED")
    finally:
        if not args.keep_open:
            bridge.shutdown()


if __name__ == "__main__":
    main()
