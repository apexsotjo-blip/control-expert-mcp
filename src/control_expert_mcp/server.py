"""MCP server exposing EcoStruxure Control Expert to AI agents.

Transport: stdio. All logs go to stderr (stdout carries JSON-RPC).
Online (live PLC) tools are disabled unless CE_MCP_ENABLE_ONLINE=1.
"""

from __future__ import annotations

import logging
import os
import sys

from mcp.server.fastmcp import FastMCP

from .bridge import ControlExpertBridge
from .modbus import ModbusClient, ModbusError, parse_address
from .prompts import register_prompts

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("control-expert-mcp")

mcp = FastMCP(
    "Control Expert",
    instructions=(
        "Automates a local EcoStruxure Control Expert (Unity Pro) installation. "
        "Typical flow: open_project (or new_project) -> inspect with "
        "get_project_structure / list_variables / read_section -> edit -> "
        "build_project (its 'output' lists per-section errors; iterate until "
        "'Process succeeded') -> save_project. Writing logic: for ST use "
        "write_st_logic (plain IEC text, no XML). For LD/FBD/SFC, FIRST call "
        "get_language_reference(language) and mirror its validated example via "
        "import_xml(kind='section'). SFC is the right choice for stepwise "
        "sequences (steps + transitions + ST actions).\n\n"
        "Live testing: online tools (plc_*, modbus_*) need CE_MCP_ENABLE_ONLINE=1. "
        "To run a project on the simulator, see the commission_simulator prompt (a "
        "fresh sim needs a one-time manual transfer from the CE GUI to seed the CPU "
        "family — 'Family check failed' otherwise). Reading/writing LIVE values is "
        "done over Modbus TCP (modbus_connect/read_tags/write_tags), not the COM API; "
        "only LOCATED %M/%MW tags are reachable, so mirror unlocated DFB internals to "
        "%MW first (see the test_logic_live prompt). Invoke a guided prompt instead of "
        "rediscovering these flows."
    ),
)

ce = ControlExpertBridge()
mb = ModbusClient()
register_prompts(mcp)

ONLINE_ENABLED = os.environ.get("CE_MCP_ENABLE_ONLINE", "").strip() in ("1", "true", "yes")


def _default_type_for(address: str) -> str:
    """Default IEC type when a raw %-address is given without one."""
    try:
        family, _, _ = parse_address(address)
    except ModbusError:
        return "INT"
    return "BOOL" if family in ("M", "MX") else "INT"


def _build_tag_plan(specs: list[str]) -> list[list]:
    """Turn tag specs into [label, address, type] rows. Each spec is a global
    variable name (address+type resolved from the project) or an explicit
    address with optional ':TYPE' (e.g. '%MW2:REAL', '%MW70:UDINT', '%M3')."""
    plan: list[list] = []
    names: list[str] = []
    for spec in specs:
        spec = spec.strip()
        if not spec:
            continue
        if spec.startswith("%"):
            addr, _, typ = spec.partition(":")
            plan.append([spec, addr.strip(), typ.strip() or _default_type_for(addr.strip())])
        else:
            names.append(spec)
            plan.append([spec, None, None])
    if names:
        resolved = ce.resolve_addresses(names)
        for row in plan:
            if row[1] is None:
                rec = resolved.get(row[0], {})
                row[1], row[2] = rec.get("address", ""), rec.get("type", "")
    return plan


# --------------------------------------------------------------- session


@mcp.tool()
def get_status() -> dict:
    """Get the current session status: Control Expert server version, whether a
    project is open, its file path, CPU, version and build state."""
    return ce.get_status()


@mcp.tool()
def open_project(path: str) -> dict:
    """Open a Control Expert project file.

    Supports .stu (native project), .sta (archive), and .xef/.zef (XML exchange
    format, imported into a fresh application). Any previously open project is
    closed without saving. Opening can take 30-120 s for large projects.
    """
    return ce.open_project(path)


@mcp.tool()
def new_project(cpu_part_number: str, cpu_version: str, project_name: str = "Project") -> dict:
    """Create a new project from scratch for a given PLC CPU.

    cpu_part_number must exactly match a CPU reference in the installed Control
    Expert hardware catalog, e.g. 'BMX P34 2020' (M340), 'BME P58 2040' (M580),
    'TSX P57 4634M' (Premium), '140 CPU 651 60' (Quantum). cpu_version is the
    firmware version offered by the catalog and is required, e.g. '02.70'.
    The project exists only in memory until save_project is called with a path.
    """
    return ce.new_project(cpu_part_number, cpu_version, project_name)


@mcp.tool()
def save_project(path: str = "") -> dict:
    """Save the open project. Pass path (ending in .stu) on first save or to
    save a copy under a new name; leave empty to save in place."""
    return ce.save_project(path or None)


@mcp.tool()
def close_project(save: bool = False) -> dict:
    """Close the open project, optionally saving first. Unsaved changes are
    discarded when save=False."""
    return ce.close_project(save)


# ----------------------------------------------------------------- build


@mcp.tool()
def build_project(rebuild_all: bool = False) -> dict:
    """Build the project (incremental by default, full rebuild with
    rebuild_all=True). Returns the resulting build state and, when available,
    the Control Expert output window text with errors/warnings. A successful
    build is required before transferring to a PLC or simulator."""
    return ce.build_project(rebuild_all)


@mcp.tool()
def get_project_setting(ident: str) -> dict:
    """Read a project setting value by its identifier (e.g.
    'unity.multiAssign', 'unity.nestedComment', 'unity.RemanentLink'). Idents
    appear as <entryvalue ident="..."> in an exported .xef."""
    return ce.get_project_setting(ident)


@mcp.tool()
def set_project_settings(settings: dict) -> dict:
    """Set one or more project settings (build/language options). Common idents:
    'unity.multiAssign'='1' allows chained assignments a:=b:=c (error E1203 if
    disabled), 'unity.nestedComment', 'unity.paramNotAssign',
    'unity.emptyParaAllowed'. Values are strings ('0'/'1' for booleans). The
    project is reloaded from a patched archive and UNSAVED — build_project then
    save_project afterwards."""
    return ce.set_project_settings({str(k): str(v) for k, v in settings.items()})


@mcp.tool()
def analyze_project() -> dict:
    """Run a syntax/semantic analysis of the project without generating code.
    Faster than a build; use it to validate edits."""
    return ce.analyze_project()


# ------------------------------------------------------------- structure


@mcp.tool()
def get_project_structure() -> dict:
    """List the program structure: tasks (MAST, FAST, ...) with their scan
    settings and the sections in each task (name + IEC language), plus counts
    of events and functional modules."""
    return ce.get_project_structure()


@mcp.tool()
def list_data_types() -> dict:
    """List user data types in the project: DFB types (function blocks) and
    DDTs (structured/array types) with their versions."""
    return ce.list_data_types()


# -------------------------------------------------------------- variables


@mcp.tool()
def list_variables(name_filter: str = "", max_results: int = 200) -> dict:
    """List global variables with type, comment, topological address (%MW...,
    %I..., etc.) and initial value. name_filter does a case-insensitive
    substring match on the variable name."""
    return ce.list_variables(name_filter or None, max_results)


@mcp.tool()
def create_variable(
    name: str,
    type_name: str,
    comment: str = "",
    address: str = "",
    initial_value: str = "",
) -> dict:
    """Create a global variable.

    type_name is an IEC type ('BOOL', 'EBOOL', 'INT', 'DINT', 'REAL', 'TIME',
    'STRING', ...), an array ('ARRAY[0..9] OF INT'), a DDT or a DFB type name.
    address is an optional topological address like '%MW100' or '%Q0.2.0'.
    """
    return ce.create_variable(
        name, type_name, comment or None, address or None, initial_value or None
    )


@mcp.tool()
def update_variable(
    name: str,
    new_name: str = "",
    comment: str = "",
    address: str = "",
    initial_value: str = "",
) -> dict:
    """Update attributes of an existing variable. Only the non-empty arguments
    are applied. To clear an attribute, pass a single space."""
    return ce.update_variable(
        name,
        new_name or None,
        comment if comment else None,
        address if address else None,
        initial_value if initial_value else None,
    )


@mcp.tool()
def delete_variable(name: str) -> dict:
    """Delete a global variable by name."""
    return ce.delete_variable(name)


# --------------------------------------------------------------- sections


@mcp.tool()
def get_language_reference(language: str) -> dict:
    """REQUIRED READING before writing any program logic: the authoring guide
    for a Control Expert language ('ST', 'LD', 'FBD', 'SFC', 'IL').

    Returns the exchange-XML structure rules (validated against a live
    Control Expert build) and a complete example section that imports and
    builds with 0 errors — mirror its shapes exactly. Workflow: read this
    guide -> write the section XML -> import_xml(kind='section') ->
    build_project -> fix any errors listed in the build output -> repeat.
    For ST, prefer the write_st_logic tool (no XML needed).
    """
    from .lang_reference import REFERENCES

    ref = REFERENCES.get(language.upper())
    if ref is None:
        raise ValueError(f"Unknown language '{language}'. Use one of {sorted(REFERENCES)}.")
    return {"language": language.upper(), **ref}


@mcp.tool()
def write_st_logic(task: str, section: str, st_source: str, declare: str = "") -> dict:
    """Write a program section in plain IEC 61131-3 Structured Text — no XML
    required. Creates the section or replaces its logic if it exists.

    st_source is raw ST (IF/CASE/FOR, FB calls like 'T1(IN := x, PT := t#3s,
    Q => y);', set()/reset() on EBOOLs, re()/fe() edges). declare optionally
    declares variables as a comma-separated 'name:TYPE' list, e.g.
    'StartPB:BOOL, Delay1:TON, Level:REAL' — FB instances called in the code
    (TON/TOF/CTU/DFB types) must exist or be declared here. Run build_project
    afterwards and fix any errors from its output.
    """
    decl = None
    if declare.strip():
        decl = {}
        for pair in declare.split(","):
            name, _, typ = pair.partition(":")
            if name.strip() and typ.strip():
                decl[name.strip()] = typ.strip()
    return ce.write_st_logic(task, section, st_source, decl)


@mcp.tool()
def read_section(task: str, section: str) -> dict:
    """Read the logic of a program section as Control Expert XML. The XML
    contains the source code (ST text, ladder rungs, FBD networks...) and is
    the same format accepted by import_xml(kind='section')."""
    return ce.read_section(task, section)


@mcp.tool()
def create_section(task: str, name: str, language: str = "ST") -> dict:
    """Create a new empty program section in a task.

    language: ST, LD, FBD, SFC, IL or LL984. To fill it with logic, follow up
    with import_xml(kind='section') using XML in the shape returned by
    read_section.
    """
    return ce.create_section(task, name, language)


@mcp.tool()
def delete_section(task: str, section: str) -> dict:
    """Delete a program section from a task."""
    return ce.delete_section(task, section)


@mcp.tool()
def create_task(task_type: str, periodicity_ms: int = 0) -> dict:
    """Add a task to the project. task_type: MAST, FAST, AUX0..AUX3 or SAFE.
    periodicity_ms > 0 makes the task periodic with that period."""
    return ce.create_task(task_type, periodicity_ms or None)


# ---------------------------------------------------------- import/export


@mcp.tool()
def import_xml(
    kind: str,
    xml_content: str = "",
    file_path: str = "",
    task: str = "",
    import_mode: str = "overwrite",
) -> dict:
    """Import Control Expert XML into the open project — the main way to write
    program logic and bulk content.

    kind: 'section' (program logic, requires task), 'variables', 'dfb', 'ddt',
    'configuration', or 'project' (generic project-level import of an exchange
    file). Provide the XML either inline via xml_content or as file_path.
    import_mode: 'overwrite' (default), 'keep_existing' or 'rename'.

    Tip: export an existing object first (read_section / export_xml) and use
    its XML as the structural template — Control Expert validates the schema
    strictly.
    """
    return ce.import_xml(xml_content or None, file_path or None, kind, task or None, import_mode)


@mcp.tool()
def export_xml(kind: str, task: str = "", name: str = "") -> dict:
    """Export project content as Control Expert XML and return it inline.

    kind: 'variables' (all variables), 'program' (all program logic),
    'configuration' (hardware config), 'dfb' (one DFB type, requires name),
    'section' (one section, requires task and name). Large exports are written
    to a temp file and the path is returned instead.
    """
    return ce.export_xml(kind, task or None, name or None)


@mcp.tool()
def export_project(path: str) -> dict:
    """Export the full application to a .xef or .zef XML exchange file —
    useful for backup, diffing, or migrating between Control Expert versions."""
    return ce.export_project(path)


# --------------------------------------------------------- animation tables


@mcp.tool()
def list_animation_tables() -> dict:
    """List the project's animation tables (watch tables used to monitor and
    force variable values online)."""
    return ce.list_animation_tables()


@mcp.tool()
def create_animation_table(name: str, variables: str) -> dict:
    """Create an animation table (or add to an existing one) and fill it with
    variables to watch. variables is a comma-separated list of variable names,
    e.g. 'Motor, StartPB, UF1_Sequence.S_Service.x, Level'. With the project
    online (simulator or PLC), the table shows live values in the Control
    Expert UI."""
    var_list = [v.strip() for v in variables.split(",") if v.strip()]
    return ce.create_animation_table(name, var_list)


@mcp.tool()
def delete_animation_table(name: str) -> dict:
    """Delete an animation table by name."""
    return ce.delete_animation_table(name)


# ---------------------------------------------------------------- hardware


@mcp.tool()
def get_hardware() -> dict:
    """List the full hardware configuration: CPU plus the bus → drop → rack →
    module tree with part numbers, versions and topological addresses."""
    return ce.get_hardware()


@mcp.tool()
def add_io_module(
    part_number: str,
    slot: int,
    version: str = "02.00",
    rack: int = 0,
    drop: int = -1,
    bus: str = "",
) -> dict:
    """Add a hardware module (IO, communication, ...) to a rack slot.

    part_number and version must exactly match the hardware catalog for the
    PLC family, e.g. 'BMX DDI 1602' (16x DI), 'BMX DDO 1602' (16x DO),
    'BMX AMI 0410' (4x AI), 'BMX NOE 0100' (Ethernet) — version '02.00' fits
    most M340 IO modules ('01.00' for some; try both). slot is the rack
    position (the CPU usually occupies slot 0). rack/drop/bus default to the
    local rack on the local bus; use get_hardware to see the topology.
    """
    return ce.add_io_module(
        part_number, slot, version, rack, None if drop < 0 else drop, bus or None
    )


@mcp.tool()
def remove_io_module(slot: int, rack: int = 0, drop: int = -1, bus: str = "") -> dict:
    """Remove the module at a rack slot."""
    return ce.remove_io_module(slot, rack, None if drop < 0 else drop, bus or None)


@mcp.tool()
def add_drop(bus: str, drop: int, part_number: str, version: str = "01.00") -> dict:
    """Add a drop to a bus — e.g. a remote X80 EIO drop on the EIO/RIO bus of an
    M580 ('M580 Drop for Ethernet'). bus is a substring of the bus name from
    get_hardware (e.g. 'EIO'); drop is the drop topo number. Then use add_rack
    and add_io_module to populate it (remote racks need their own power
    supply)."""
    return ce.add_drop(bus, drop, part_number, version)


@mcp.tool()
def add_rack(bus: str, drop: int, rack: int, part_number: str, version: str = "01.00") -> dict:
    """Add a rack to a drop (e.g. 'BME XBP 1200' in a remote EIO drop). Then
    add a power supply and modules with add_io_module (set drop/bus to target
    the remote rack; remote drops start with a CRA adapter in slot 0)."""
    return ce.add_rack(bus, drop, rack, part_number, version)


@mcp.tool()
def replace_io_module(
    slot: int,
    old_part_number: str,
    old_version: str,
    new_part_number: str,
    new_version: str,
    rack: int = 0,
    drop: int = -1,
    bus: str = "",
) -> dict:
    """Replace a module in place (keeps the slot) — e.g. swap the default power
    supply new_project puts on the local rack. Old part number/version must
    match the current module exactly (see get_hardware)."""
    return ce.replace_io_module(
        slot, old_part_number, old_version, new_part_number, new_version,
        rack, None if drop < 0 else drop, bus or None,
    )


@mcp.tool()
def replace_rack(
    rack: int,
    old_part_number: str,
    old_version: str,
    new_part_number: str,
    new_version: str,
    drop: int = -1,
    bus: str = "",
) -> dict:
    """Replace a rack in place (e.g. the default BME XBP 0800 new_project
    creates -> BME XBP 0400). Modules on the rack are kept where the new rack
    has the same slots."""
    return ce.replace_rack(
        rack, old_part_number, old_version, new_part_number, new_version,
        None if drop < 0 else drop, bus or None,
    )


@mcp.tool()
def change_cpu(part_number: str, version: str) -> dict:
    """Replace the project's CPU with another from the hardware catalog
    (e.g. 'BMX P34 2020' + '02.70')."""
    return ce.change_cpu(part_number, version)


# -------------------------------------------------------------- DTM / Modbus


@mcp.tool()
def list_dtms() -> dict:
    """List the project's DTM topology (DTM Browser): communication/master
    DTMs and their slave devices, with names, DTM ids, types, and bus
    addresses (IP for Modbus TCP / EtherNet/IP devices)."""
    return ce.list_dtms()


@mcp.tool()
def add_dtm(
    device_type_name: str,
    dtm_name: str,
    parent_dtm: str = "",
    protocol_id: str = "",
    prog_id: str = "",
    version: str = "",
) -> dict:
    """Add a DTM to the project topology.

    Top-level communication DTM: leave parent_dtm empty (e.g. on M580 the CPU
    DTM 'BMEP58_ECPU_EXT' usually exists already). Slave device under a master
    DTM: set parent_dtm to the master's name and device_type_name to the
    catalog entry — e.g. add a generic Modbus TCP slave with
    device_type_name='Modbus Device' under the M580 CPU DTM. After adding,
    set its IP with set_dtm_address and configure scan requests with
    get/set_dtm_dataset or set_dtm_control_parameters.
    """
    return ce.add_dtm(
        device_type_name, dtm_name, parent_dtm or None, protocol_id or None,
        prog_id or None, version or None,
    )


@mcp.tool()
def delete_dtm(name: str) -> dict:
    """Delete a DTM (and its children) from the topology by name."""
    return ce.delete_dtm(name)


@mcp.tool()
def set_dtm_address(
    name: str, address: str = "", gateway: str = "", subnet: str = ""
) -> dict:
    """Set a slave DTM's bus address — for Modbus TCP / EtherNet/IP devices
    this is the IP address (e.g. '192.168.10.21'); for fieldbus slaves the
    node number. Optionally also set the device's gateway and subnet mask
    (the gateway must be in the same IP domain as the address or the build
    warns 'IP Address and Gateway address are not in the same domain').
    Setting gateway/subnet rewrites the project archive: the project is
    reloaded and UNSAVED — build_project then save_project afterwards."""
    return ce.set_dtm_address(name, address, gateway or None, subnet or None)


@mcp.tool()
def get_dtm_control_parameters(name: str) -> dict:
    """Read a master/communication DTM's control parameters as XML
    (CetControlParameterOutput schema): Modbus TCP I/O scanner lines with IP,
    unit id, timeouts, repetitive rate, and the ModbusTcpRequest entries
    (read/write start addresses and sizes)."""
    return ce.get_dtm_control_parameters(name)


@mcp.tool()
def set_dtm_control_parameters(name: str, xml: str, build: bool = True) -> dict:
    """Write a master DTM's control parameters (CetControlParameterInput XML)
    — scanner sizing and %MW mapping of the I/O scan. With build=True the
    DTM→PLC control information is rebuilt afterwards. Read the current
    values with get_dtm_control_parameters first and mirror the schema."""
    return ce.set_dtm_control_parameters(name, xml, build)


@mcp.tool()
def get_dtm_dataset(name: str) -> dict:
    """Export a DTM's full configuration dataset. For M580 master DTMs this is
    the XML dataset whose <SlaveDevices>/<ManagedModbusRequest> nodes hold the
    Modbus scan lines (request read/write addresses, lengths, connection
    numbers, IO item names) — edit it and write back with set_dtm_dataset."""
    return ce.get_dtm_dataset(name)


@mcp.tool()
def set_dtm_dataset(name: str, xml: str) -> dict:
    """Import a slave DTM configuration dataset (previously exported with
    get_dtm_dataset, possibly modified)."""
    return ce.set_dtm_dataset(name, xml)


@mcp.tool()
def get_master_dtm_dataset(dtm: str = "") -> dict:
    """Read the master/CPU DTM dataset XML — THE document holding the Modbus
    TCP scan lines (M580 DTMs XML Dataset spec).

    Each scanned device is a <SlaveDevice deviceTag="..."> under
    <SlaveDevices>; its <ModbusTCP><ManagedModbusRequestList> holds one
    <ManagedModbusRequest Index="N"> per scan line with Parameters attributes
    requestSettingBit (connection number), requestSettingRDAddress/RDLength,
    requestSettingWRAddress/WRLength, requestInputObjID/requestOutputObjID.
    dtm defaults to the first (CPU) DTM node.
    """
    return ce.get_master_dtm_dataset(dtm or None)


@mcp.tool()
def set_master_dtm_dataset(xml: str, dtm: str = "") -> dict:
    """Write back a modified master DTM dataset (adds/edits Modbus scan
    lines) and reload the project from the modified exchange archive.

    To add a Modbus scan request, insert into the target SlaveDevice's
    <ManagedModbusRequestList> a node shaped exactly like this (validated):

      <ManagedModbusRequest Index="0" State="0" FromExtTool="1">
        <Parameters requestSettingBit="1" requestSettingUnitID="255"
          requestSettingTimeout="1500" requestSettingRepetitiveRate="60"
          requestSettingRDAddress="0" requestSettingRDLength="10"
          requestSettingLastValue="0" requestSettingWRAddress="0"
          requestSettingWRLength="10" requestUniqueID="<NEW RANDOM GUID>"
          requestInputObjID="1024" requestOutputObjID="1025"
          requestReservedInputObjID="65535" requestReservedOutputObjID="1025"/>
      </ManagedModbusRequest>

    Rules (M580 CPU scanner): Index increments per request within the device
    starting at 0; requestSettingBit is the connection number (Modbus range
    1-128, unique across the scanner); requestUniqueID must be a fresh GUID;
    requestInput/OutputObjID use an even/odd pair (n, n+1) from 1024-1279 per
    connection, 65535 for an unused direction; RD/WR address+length are in
    words (slave-index format, 0-based). After this call the project is
    reloaded and UNSAVED — run build_project to validate (the scan line then
    appears in get_dtm_control_parameters), then save_project.
    """
    return ce.set_master_dtm_dataset(xml, dtm or None)


@mcp.tool()
def configure_cpu_ethernet(
    ip: str = "",
    subnet: str = "",
    gateway: str = "",
    ip_a: str = "",
    ip_b: str = "",
    ip_d: str = "",
    enable_tftp: int = -1,
    enable_eip: int = -1,
    enable_dhcp_bootp: int = -1,
    enable_ftp: int = -1,
    enable_web: int = -1,
    enable_snmp: int = -1,
) -> dict:
    """Configure the M580 CPU's embedded Ethernet: main IP/subnet/gateway
    (+ ip_a/ip_b for the HSBY A/B addresses and ip_d for address D) and the
    Security-screen service flags.

    Service args: -1 = leave unchanged, 0 = disable, 1 = enable. A remote EIO
    drop's CRA requires tftp, eip and dhcp_bootp ENABLED, otherwise the build
    fails with "CRA doesn't work when TFTP, EIP and DHCP_BOOTP settings are
    disabled". Updates every stored copy (application channel config words,
    DTM binary parameter container, DTM dataset XML) via a project-archive
    round-trip, so the project is reloaded and UNSAVED — build_project to
    validate, then save_project. For a slave device's gateway-domain warning
    use set_dtm_address(gateway=...) instead.
    """
    services: dict[str, bool] = {}
    for key, val in (
        ("tftp", enable_tftp), ("eipServer", enable_eip),
        ("dhcp_bootp", enable_dhcp_bootp), ("ftp", enable_ftp),
        ("webServer", enable_web), ("snmp", enable_snmp),
    ):
        if val in (0, 1):
            services[key] = bool(val)
    return ce.configure_cpu_ethernet(
        ip or None, subnet or None, gateway or None, ip_a or None, services,
        ip_b or None, ip_d or None,
    )


@mcp.tool()
def start_simulator(enforce_security: bool = False) -> dict:
    """Start the Control Expert PLC simulator (sim.exe) so plc_connect can
    reach it. With enforce_security=False (default) the simulator's 'use
    default application (enforce security)' option is disabled in the registry
    first — otherwise sim.exe blocks on a warning dialog unless a
    password-protected default application is configured. Only use on a local
    test machine."""
    return ce.start_simulator(enforce_security)


@mcp.tool()
def stop_simulator() -> dict:
    """Stop the PLC simulator process."""
    return ce.stop_simulator()


# ---------------------------------------------------------------- networks


@mcp.tool()
def list_networks() -> dict:
    """List logical networks (Premium/Quantum: Ethernet, Modbus Plus, Fipway)
    with their IP service configuration where available."""
    return ce.list_networks()


@mcp.tool()
def add_network(name: str, family: str) -> dict:
    """Create a logical network (Premium/Quantum platforms; family e.g.
    'Ethernet'). On M340/M580, configure communication on modules/DTMs
    instead."""
    return ce.add_network(name, family)


@mcp.tool()
def set_network_ip(name: str, ip_address: str, subnet_mask: str, gateway: str) -> dict:
    """Set the static IP configuration of a logical Ethernet network."""
    return ce.set_network_ip(name, ip_address, subnet_mask, gateway)


# --------------------------------------------------------------------- UI


@mcp.tool()
def show_ui(state: str = "show_normal", mode: str = "read_only", command_line: str = "") -> dict:
    """Make the Control Expert window of this automation session visible so a
    human can watch the AI work or take over. state: show_normal,
    show_maximized, minimize, restore.

    mode: 'read_only' (default — the GUI follows along while this client keeps
    the write token; required for opening editors) or 'read_write' (hand the
    GUI full control; only works if this client does not hold write access).
    command_line: optional Control Expert command line to open specific editors
    on startup."""
    return ce.show_ui(state, mode, command_line)


@mcp.tool()
def open_animation_table(name: str, state: str = "show_normal") -> dict:
    """Open an existing animation table's editor inside the live Control Expert
    window, so a human watches values update in real time while the AI drives a
    test. Typical live-test flow: create_animation_table -> start_simulator ->
    plc_connect -> plc_transfer -> plc_command('run') -> open_animation_table.
    The GUI opens read-only (this client keeps write); the table animates live
    once connected to the simulator/PLC."""
    return ce.open_animation_table(name, state)


# ----------------------------------------------------------------- online

if ONLINE_ENABLED:

    @mcp.tool()
    def plc_setup_connection(target: str = "simulator", address: str = "", driver: str = "") -> dict:
        """Configure the connection target. target: 'simulator' or 'plc'.
        address e.g. '127.0.0.1' or '192.168.10.1'; driver e.g. 'TCPIP'.
        EXPERIMENTAL — affects equipment when target='plc'."""
        return ce.plc_setup_connection(target, address or None, driver or None)

    @mcp.tool()
    def plc_connect(target: str = "simulator", mode: str = "primary") -> dict:
        """Connect to the PLC or simulator. The project must be built first."""
        return ce.plc_connect(target, mode)

    @mcp.tool()
    def plc_disconnect() -> dict:
        """Disconnect from the PLC/simulator."""
        return ce.plc_disconnect()

    @mcp.tool()
    def plc_state() -> dict:
        """Report connection state and PLC run/stop state."""
        return ce.plc_state()

    @mcp.tool()
    def plc_transfer(direction: str = "pc_to_plc") -> dict:
        """Transfer the application. direction: 'pc_to_plc' (download to
        controller — DANGEROUS on live equipment, PLC must be stopped) or
        'plc_to_pc' (upload from controller)."""
        return ce.plc_transfer(direction)

    @mcp.tool()
    def plc_command(command: str) -> dict:
        """Send a run/stop/init command to the connected PLC. DANGEROUS:
        starting or stopping a live controller affects the physical process."""
        return ce.plc_command(command)

    # ------------------------------------- live values over Modbus TCP

    @mcp.tool()
    def modbus_connect(host: str, port: int = 502, unit: int = 1, word_order: str = "low_first") -> dict:
        """Open a Modbus TCP link to a Modicon CPU's embedded server for live
        read/write of LOCATED tags (%M / %MW) while the PLC runs — the channel
        SCADA uses, and the only way this server reads/writes live values (the
        UDE/COM API cannot).

        Only located tags work: unlocated DFB internals (e.g. Pump1.Running)
        must be mirrored to a %MW/%M address in the program first. word_order is
        for 32-bit REAL/DINT: 'low_first' (Schneider default) or 'high_first'.
        Works against the SIMULATOR (host='127.0.0.1', the endpoint a Vijeo
        Designer I/O scanner reaches) as well as a real CPU IP; port 502."""
        return mb.connect(host, port, unit, word_order)

    @mcp.tool()
    def modbus_disconnect() -> dict:
        """Close the Modbus TCP connection."""
        return mb.disconnect()

    @mcp.tool()
    def modbus_status() -> dict:
        """Report the Modbus TCP connection state and decode settings."""
        return mb.status()

    @mcp.tool()
    def read_tags(tags: str) -> dict:
        """Read live values of located tags over Modbus TCP (modbus_connect
        first). tags is a comma-separated list; each item is a global variable
        name (its %address and IEC type are looked up from the project) or an
        explicit address '%MW70' / '%M3' / '%MW10.2', optionally typed
        '%MW2:REAL' or '%MW70:UDINT' (default INT for %MW, BOOL for %M).
        Returns {values:{tag:value}} and any per-tag errors."""
        plan = _build_tag_plan(tags.split(","))
        values, errors = {}, {}
        for label, addr, typ in plan:
            if not addr:
                errors[label] = "no located %M/%MW address (unknown name or unlocated tag)"
                continue
            try:
                values[label] = mb.read_one(addr, typ)
            except ModbusError as exc:
                errors[label] = str(exc)
        out: dict = {"values": values}
        if errors:
            out["errors"] = errors
        return out

    @mcp.tool()
    def write_tags(values: dict) -> dict:
        """Write live values to located tags over Modbus TCP (modbus_connect
        first). DANGEROUS — changes a running controller's process.

        values maps each tag (a global variable name, or an address like
        '%MW86' / '%M0' / '%MW2:REAL') to the value to write. Booleans go to %M
        coils or %MWi.j bits; numbers to %MW (INT, 1 word) or two %MW words
        (REAL/DINT/UDINT). Returns {written:{tag:value}} and any per-tag errors."""
        plan = _build_tag_plan(list(values.keys()))
        written, errors = {}, {}
        for label, addr, typ in plan:
            if not addr:
                errors[label] = "no located %M/%MW address (unknown name or unlocated tag)"
                continue
            try:
                mb.write_one(addr, typ, values[label])
                written[label] = values[label]
            except ModbusError as exc:
                errors[label] = str(exc)
        out: dict = {"written": written}
        if errors:
            out["errors"] = errors
        return out


def main() -> None:
    log.info(
        "Starting Control Expert MCP server (online tools %s)",
        "ENABLED" if ONLINE_ENABLED else "disabled — set CE_MCP_ENABLE_ONLINE=1 to enable",
    )
    mcp.run()


if __name__ == "__main__":
    main()
