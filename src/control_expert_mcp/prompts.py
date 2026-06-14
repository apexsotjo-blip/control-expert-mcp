"""Guided-workflow prompts for the Control Expert MCP server.

MCP prompts are reusable recipes a client can invoke (in Claude Code they show
up as /mcp__control-expert__<name> slash commands). They encode the validated
flows — and the non-obvious gotchas — so a client doesn't rediscover them by
trial and error.

CONVENTION FOR CONTRIBUTORS: when you add a tool or capability, add (or extend)
a prompt here that walks a client through using it, especially any step that is
not guessable from the tool description alone (an env flag, a manual GUI step,
an ordering constraint, an addressing rule). A good prompt turns an hour of
trial-and-error into one slash command. See README "Extending the server".

All functions return a plain instruction string (FastMCP delivers it as the
prompt's user message). Register them with register_prompts(mcp).
"""

from __future__ import annotations


def register_prompts(mcp) -> None:
    @mcp.prompt()
    def commission_simulator() -> str:
        """Bring the open project up and running on the Control Expert PLC simulator."""
        return (
            "Goal: get the currently open project running on the Control Expert simulator.\n\n"
            "1. build_project — must reach build_state 'built_ok' before any transfer "
            "(fix errors from the 'output' field and rebuild).\n"
            "2. start_simulator — launches sim.exe (loopback).\n"
            "3. plc_setup_connection(target='simulator', address='127.0.0.1', driver='TCPIP').\n"
            "4. plc_connect(target='simulator').\n"
            "5. plc_command('stop')  — a transfer is rejected if the PLC is running.\n"
            "6. plc_transfer(direction='pc_to_plc').\n"
            "7. plc_command('run'); confirm with plc_state (expect plc_state='run', "
            "pc_equals_plc=true).\n\n"
            "GOTCHA — 'Family check failed' on plc_transfer: the API download requires the "
            "simulator to ALREADY hold a station of the SAME CPU family. A fresh sim "
            "(plc_state='no_conf') or one running a different-family station rejects it. "
            "Seed it ONCE manually in the Control Expert GUI (PLC -> Simulation Mode -> "
            "Connect -> Transfer); the station persists while sim.exe runs and API "
            "downloads work afterwards. This also covers M580 Hot-Standby CPUs.\n"
            "NOTE: requires the server started with CE_MCP_ENABLE_ONLINE=1 (the plc_* tools "
            "are hidden otherwise)."
        )

    @mcp.prompt()
    def test_logic_live(instance: str = "Pump1", task: str = "MAST") -> str:
        """Live-test a DFB instance by writing inputs and reading outputs over Modbus TCP."""
        return (
            f"Goal: drive and observe the '{instance}' logic live on the running PLC/simulator.\n\n"
            "Live values travel over Modbus TCP (the UDE/COM API has NO live tag access). "
            "First make sure the project is RUNNING on the target — see the "
            "commission_simulator prompt.\n\n"
            "KEY CONSTRAINT: Modbus only reaches LOCATED tags (%M / %MW). A DFB instance's "
            f"fields (e.g. {instance}.Running, {instance}.ManStartCmd, {instance}.SpdRefAO) are "
            "UNLOCATED internals and are invisible to Modbus until mirrored. So:\n\n"
            f"1. Add a small mirror section in task '{task}' (write_st_logic) that bridges the "
            "fields you want to drive/observe to located addresses, e.g.:\n"
            f"     {instance}.ManStartCmd := %MX100.0;   {instance}.Auto := %MX100.1;\n"
            f"     %MX110.0 := {instance}.Running;  %MX110.1 := {instance}.Starting;\n"
            f"     %MX110.2 := {instance}.FTS_Alarm;  %MW111 := REAL_TO_INT({instance}.SpdScaled);\n"
            "   Declare the %MX/%MW tags (create_variable) or use existing located vars.\n"
            "2. build_project, then transfer + run (commission_simulator).\n"
            "3. modbus_connect(host='127.0.0.1')  (or the real CPU IP; port 502).\n"
            "4. write_tags({'%MX100.1': false, '%MX100.0': true})  — set manual mode, then start.\n"
            "5. read_tags('%MX110.0, %MX110.1, %MX110.2, %MW111')  — watch the state machine "
            "react (Stopped -> Starting -> Running; SpdScaled tracking).\n\n"
            "GOTCHA — 32-bit REAL/DINT word order: default is Schneider 'low_first'. If a REAL "
            "reads garbage, reconnect with word_order='high_first'.\n"
            "Optional: create_animation_table + open_animation_table to watch the same tags "
            "live in the Control Expert window while you drive them."
        )

    @mcp.prompt()
    def author_logic(language: str = "ST") -> str:
        """Write a program section in a given IEC language and get it building clean."""
        lang = (language or "ST").upper()
        common = (
            "Loop until the build is clean:\n"
            "  a. write/import the section\n"
            "  b. build_project\n"
            "  c. read the 'output' field — it lists per-section errors; fix and repeat "
            "until 'Process succeeded'.\n"
            "Declare referenced variables first (create_variable) or include a <dataBlock>.\n"
        )
        if lang == "ST":
            return (
                "Goal: author a Structured Text section.\n\n"
                "Use write_st_logic(task, section, st_source, declare='name:TYPE, ...') — plain "
                "IEC text, no XML. FB instances (TON/TOF/CTU/DFB types) must be declared.\n\n" + common
            )
        return (
            f"Goal: author a {lang} section (graphical/textual).\n\n"
            f"1. get_language_reference('{lang}') FIRST — it returns the exchange-XML structure "
            "rules and a validated example that builds with 0 errors. Mirror its shapes exactly "
            "(FBD pin geometry, LD cell model, SFC chart layout).\n"
            "2. import_xml(kind='section', task=..., xml_content=...).\n" + common +
            "SFC is the right choice for stepwise sequences (steps + transitions + ST actions)."
        )

    @mcp.prompt()
    def scaffold_project(cpu_part_number: str = "BME P58 2040", cpu_version: str = "") -> str:
        """Create a new project for a CPU and build out a starter rack/IO configuration."""
        ver = cpu_version or "<exact catalog version, e.g. 02.70 / 03.20>"
        return (
            f"Goal: scaffold a new project on CPU '{cpu_part_number}'.\n\n"
            f"1. new_project(cpu_part_number='{cpu_part_number}', cpu_version='{ver}'). The part "
            "number AND version must EXACTLY match this Control Expert version's hardware catalog "
            "(spacing matters); 'Catalog object not found' means no exact match — verify in the GUI.\n"
            "2. get_hardware to see the auto-created local rack/PSU/CPU topology.\n"
            "3. add_io_module(part_number, slot, version) for each module (version is mandatory: "
            "'02.00' for most M340 IO, '01.00' for racks/PSUs; try both on failure). Use "
            "add_drop/add_rack for remote EIO drops.\n"
            "4. create_variable / write_st_logic to add data and logic (see author_logic).\n"
            "5. build_project until 'built_ok', then save_project('C:/path/MyApp.stu').\n\n"
            "Tip: prefer a standalone CPU for simulator work; HSBY needs a manual first transfer "
            "(see commission_simulator)."
        )

    @mcp.prompt()
    def add_modbus_device(parent_dtm: str = "BMEP58_ECPU_EXT") -> str:
        """Add a Modbus-TCP slave device under an M580 CPU DTM and configure a scan line."""
        return (
            "Goal: add a Modbus TCP slave to the M580 scanner and give it a read/write scan line.\n\n"
            f"1. add_dtm(device_type_name='Modbus Device', dtm_name='MyDevice', "
            f"parent_dtm='{parent_dtm}')  — protocol_id 'Modbus' is auto-tried.\n"
            "2. set_dtm_address('MyDevice', address='192.168.10.21', gateway=..., subnet=...) "
            "(gateway must share the IP domain or the build warns).\n"
            "3. get_master_dtm_dataset to see the <SlaveDevices>/<ManagedModbusRequestList>; add a "
            "<ManagedModbusRequest> (mirror the template + numbering rules in the "
            "set_master_dtm_dataset tool description: Index from 0, unique requestSettingBit "
            "connection number, fresh GUID requestUniqueID, even/odd Input/OutputObjID pair).\n"
            "4. set_master_dtm_dataset(xml) — reloads the project UNSAVED.\n"
            "5. build_project (the line appears in get_dtm_control_parameters as a ModbusScanLine), "
            "then save_project."
        )
