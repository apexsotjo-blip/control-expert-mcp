# Control Expert MCP Server

An [MCP](https://modelcontextprotocol.io/) server that connects AI agents (Claude Desktop, Claude Code, VS Code Copilot, or any MCP client) to a local **EcoStruxure Control Expert** (formerly **Unity Pro**) installation — so the AI can **read, edit, and create PLC projects from scratch**.

Inspired by [tiaportal-mcp](https://github.com/heilingbrunner/tiaportal-mcp) for Siemens TIA Portal. Where TIA Portal exposes the Openness API, Control Expert exposes the **Unity Developer's Edition (UDE) COM automation server** (`PSBroker.PServerBroker`) — this server drives it through `pywin32` and exposes it as MCP tools over stdio.

```
AI agent (Claude, Copilot, ...) ──MCP/stdio──> control-expert-mcp ──COM──> Control Expert
```

## What the AI can do

- **Projects**: create from scratch for any CPU in the hardware catalog (M340, M580, Premium, Quantum...), open/save `.stu`/`.sta`, import/export `.xef`/`.zef`
- **Browse**: tasks, sections, variables, DFB/DDT types, CPU/hardware info, build state
- **Program**: create/read/delete sections in **ST, LD, FBD, SFC, IL**; write logic by importing Control Expert XML (the AI reads an existing section's XML once and mirrors the schema — validated round-trip for all five languages)
- **Hardware**: walk the full bus → drop → rack → module topology, **add/remove IO modules** by catalog part number, change the CPU
- **Modbus / DTMs** (validated live on M580): browse the DTM topology, add slave DTMs (`add_dtm("Modbus Device", ..., parent_dtm="BMEP58_ECPU_EXT")` — the protocol id is `Modbus` and is auto-tried), **set device IP addresses**, read the scanner state (`get_dtm_control_parameters`), and **add Modbus scan requests** (read/write addresses + sizes) by editing the master DTM dataset through `get_master_dtm_dataset` / `set_master_dtm_dataset` — a `ManagedModbusRequest` template with the numbering rules is built into the tool description; a written scan line builds `built_ok` and shows up as a `ModbusScanLine` in the scanner config
- **Networks** (Premium/Quantum): create logical Ethernet networks and set their IP service configuration
- **Variables**: list/create/update/delete global variables incl. type, comment, address (`%MW...`), initial value
- **Build**: analyze and build the project, get the resulting build state
- **UI**: pop the Control Expert window open so a human can watch or take over, and open an animation table's editor live (`open_animation_table`) to watch values animate while the AI drives a test
- **Online** (opt-in): connect to PLC/simulator, download/upload, run/stop
- **Live values** (opt-in): read and write running PLC/simulator values over **Modbus TCP** (`modbus_connect` → `read_tags`/`write_tags`) — the UDE/COM API has no live tag access, so this is the channel for testing logic against a running controller (located `%M`/`%MW` tags; mirror unlocated DFB internals to `%MW` first)

## Requirements

- Windows with **EcoStruxure Control Expert** (or Unity Pro) installed and licensed — tested against **Control Expert 14.0**, but any version that registers `PSBroker.PServerBroker.1` should work (the UDE automation server ships with Control Expert itself; the separate UDE package is only needed for documentation)
- **Python 3.10+** with `pywin32`
- The server must run **on the same machine** as Control Expert (DCOM remoting is possible but not configured here)

## Install

```powershell
cd control-expert-mcp
python -m venv .venv
.venv\Scripts\pip install -e .
```

## Hook up an AI client

The server speaks MCP over stdio. Point your client at the venv's Python:

### Claude Desktop

`%APPDATA%\Claude\claude_desktop_config.json` (see [samples/claude_desktop_config.json](samples/claude_desktop_config.json)):

```json
{
  "mcpServers": {
    "control-expert": {
      "command": "C:\\path\\to\\control-expert-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "control_expert_mcp"]
    }
  }
}
```

### Claude Code

```powershell
claude mcp add control-expert -- C:\path\to\control-expert-mcp\.venv\Scripts\python.exe -m control_expert_mcp
```

### VS Code (GitHub Copilot agent mode)

`.vscode/mcp.json` (see [samples/vscode-mcp.json](samples/vscode-mcp.json)):

```json
{
  "servers": {
    "control-expert": {
      "type": "stdio",
      "command": "C:\\path\\to\\control-expert-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "control_expert_mcp"]
    }
  }
}
```

## Tools

| Tool | Purpose |
| --- | --- |
| `get_status` | Server version, open project, CPU, build state |
| `open_project` | Open `.stu` / `.sta` / `.xef` / `.zef` |
| `new_project` | Create a project for a CPU (e.g. `BMX P34 2020` + `02.70`) |
| `save_project` / `close_project` | Persist / close (close discards unless `save=true`) |
| `build_project` / `analyze_project` | Build or analyze; returns resulting build state |
| `get_project_structure` | Tasks, sections (+language), events, fct modules |
| `list_variables` | Variables with type/comment/address/initial value |
| `create_variable` / `update_variable` / `delete_variable` | Variable editing |
| `read_section` | Section logic as Control Expert XML |
| `write_st_logic` | **Write ST logic as plain IEC text** (no XML) — creates or replaces a section, with inline variable declarations |
| `get_language_reference` | Authoring guide + validated example for ST/LD/FBD/SFC/IL — the exchange-XML structure rules an AI client needs to write graphical logic (FBD pin-geometry rule, LD line/cell model, SFC chart layout) |
| `validate_xml` | **Pre-validate** candidate exchange-XML against the installed `SrcXmlSchema` XSDs before import/build — instant structural errors with the legal value sets |
| `place_fb_in_ladder` | **Drop any project DFB into Ladder, no template** — reads the DFB interface and generates the correct (CE-owned) pin geometry automatically, then bind pins |
| `use_fb_in_ladder` | Clone a GUI-authored block-in-LD template and rebind instance + variables (fallback for elementary EFBs / exact GUI geometry) |
| `create_section` / `delete_section` | Section management (ST/LD/FBD/SFC/IL) |
| `create_task` | Add MAST/FAST/AUX/SAFE task |
| `import_xml` | **The main write path** — import section logic, variables, DFB/DDT types, configuration, or whole-project exchange files (inline XML or file) |
| `export_xml` | Export variables / program / configuration / one DFB / one section as XML |
| `export_project` | Full application export to `.xef` / `.zef` |
| `list_data_types` | DFB + DDT types |
| `get_hardware` | CPU + bus → drop → rack → module tree |
| `add_io_module` / `remove_io_module` | Add/remove rack modules by part number + catalog version (e.g. `BMX DDI 1602` + `02.00`) |
| `add_drop` / `add_rack` | Build out remote drops/racks (e.g. an X80 EIO drop + `BME XBP 1200` rack on an M580 RIO bus) |
| `change_cpu` | Swap the CPU reference |
| `list_animation_tables` / `create_animation_table` / `delete_animation_table` | Watch/animation tables (accepts hierarchical paths like `Pump1.Speed`, `SFC_Demo.S_Init.x`) for monitoring/forcing values online |
| `list_dtms` | DTM Browser topology with names, types, addresses |
| `add_dtm` / `delete_dtm` | Add communication DTMs or slave devices (e.g. `Modbus Device` under the M580 CPU DTM) |
| `set_dtm_address` | Set a slave's IP / bus address (+ optional gateway/subnet — fixes the "IP Address and Gateway address are not in the same domain" build warning) |
| `configure_cpu_ethernet` | M580 CPU embedded Ethernet: IP/subnet/gateway + Security-screen services (tftp/eip/dhcp_bootp/ftp/web/snmp). Enabling tftp+eip+dhcp_bootp clears the remote-EIO CRA build error |
| `get_dtm_control_parameters` / `set_dtm_control_parameters` | Modbus TCP I/O scanner config XML (`CetControlParameter*` schema): IP, unit id, timeouts, `ModbusTcpRequest` scan lines |
| `get_dtm_dataset` / `set_dtm_dataset` | A slave DTM's own dataset (identity + bus address) |
| `get_master_dtm_dataset` / `set_master_dtm_dataset` | The master/CPU DTM dataset (via ZEF round-trip) — `<SlaveDevices>/<ManagedModbusRequestList>` holds the **Modbus scan lines**; write a `ManagedModbusRequest` node to add a request |
| `list_networks` / `add_network` / `set_network_ip` | Logical networks on Premium/Quantum |
| `show_ui` | Make the Control Expert window visible (read-only by default so it can follow along / open editors while the client keeps write) |
| `open_animation_table` | Open an animation table's editor in the live CE window so a human watches values animate during a test |
| `plc_*` (opt-in) | Online: setup/connect/disconnect/state/transfer/run/stop |
| `modbus_connect` / `modbus_disconnect` / `modbus_status` (opt-in) | Open/close a Modbus TCP link to the CPU/simulator's server (`127.0.0.1:502` for the sim) for live values |
| `read_tags` / `write_tags` (opt-in) | Read/write LIVE located-tag values (`%M`/`%MW`) by name or address (`%MW86`, `%MW2:REAL`); the only live read/write path (COM API has none) |

### How the AI writes logic

Program sections are exchanged as Control Expert XML. An ST section looks like:

```xml
<STExchangeFile>
  <fileHeader company="Schneider Automation" product="Control Expert V14.0 - 190112"
              dateTime="date_and_time#2026-6-11-1:0:0" content="Structured source file"
              DTDVersion="41"></fileHeader>
  <contentHeader name="Project" version="0.0.000"></contentHeader>
  <program>
    <identProgram name="Logic01" type="section" task="MAST"></identProgram>
    <STSource>
IF StartButton AND NOT StopButton THEN
    MotorRun := TRUE;
ELSE
    MotorRun := FALSE;
END_IF;
    </STSource>
  </program>
</STExchangeFile>
```

`import_xml(kind="section", xml_content=...)` creates the section if it doesn't exist, or deletes-and-replaces it on conflict. The agent should declare referenced variables first (`create_variable`) or include a `<dataBlock>`, then `build_project` to validate.

## Online tools (live PLC / simulator) — disabled by default

Tools that touch a controller (`plc_connect`, `plc_transfer`, `plc_command`, ...) are **not registered** unless you set the environment variable:

```
CE_MCP_ENABLE_ONLINE=1
```

Starting/stopping a PLC or downloading an application affects the physical process. Only enable this on test benches or with the simulator, and keep a human in the loop.

**Simulator commissioning** (validated end to end): launch the PLC simulator first — `start_simulator` or `PLC_Simulator\sim.exe` from the Control Expert install (it sits in the tray) — then `plc_setup_connection("simulator", "127.0.0.1")` → `plc_connect("simulator")` → `plc_transfer("pc_to_plc")` → `plc_command("run")`. The project must build clean (`built_ok`) before download. `plc_state` reports connection + run/stop and whether PC == PLC.

> **Known limitation:** a freshly started simulator with *no station loaded* (`plc_state` = `no_conf`) rejects `plc_transfer` with *"Family check failed"* — the API download (unlike the Control Expert GUI's) requires the sim to already have a station of a matching family. Seed it once by transferring any project from the Control Expert GUI (PLC → Simulation Mode → Connect → Transfer); the loaded station persists while sim.exe runs, and all API downloads work from then on (stop the PLC first — transfer to a running PLC fails).

### Live values over Modbus TCP (validated end to end)

The UDE/COM automation API serves the *project database* — it has **no live tag-value read/write** (animation tables only render in the GUI; `IVariable` exposes only the offline initial value). Live values go over the CPU's **Modbus TCP server** instead — the same channel SCADA/Vijeo use:

```
modbus_connect(host="127.0.0.1")          # the sim's Modbus server; or a real CPU IP, port 502
read_tags("EMFM1FLOW, RESIDUAL2CLTHSP, %MW0, %MW2:REAL")
write_tags({"RESIDUAL2CLTHSP": 4.0, "%MX100.0": true})
```

- Tags are global variable **names** (address + IEC type resolved from the project) or explicit **addresses** with optional `:TYPE` (`%MW86`, `%MW2:REAL`, `%MW70:UDINT`, `%M3`, `%MW10.2` bit). Decoding is type-driven (INT/UINT, DINT/UDINT, REAL, BOOL coil/word-bit).
- **Only LOCATED `%M`/`%MW` tags are reachable.** Unlocated DFB internals (e.g. `Pump1.Running`) must be mirrored to `%MW`/`%M` in the program first (see the `test_logic_live` prompt).
- 32-bit `REAL`/`DINT` use **Schneider low-word-first** order by default; pass `word_order="high_first"` if a server differs.
- The Control Expert **simulator exposes a Modbus server on `127.0.0.1:502`**, so the whole read/write test loop works against the sim — no hardware required.

## Prompts (guided workflows)

The server ships **MCP prompts** ([src/control_expert_mcp/prompts.py](src/control_expert_mcp/prompts.py)) — reusable recipes that encode the validated flows *and their non-obvious gotchas* so a client doesn't rediscover them by trial and error. In Claude Code they appear as `/mcp__control-expert__<name>` slash commands; other MCP clients list them in their prompt picker.

| Prompt | What it walks you through |
| --- | --- |
| `commission_simulator` | Build → start sim → connect → transfer → run, incl. the **manual first-transfer / "Family check failed"** seed step |
| `test_logic_live` | The Modbus test loop for a DFB instance, incl. **mirroring unlocated internals to `%MW`** and word-order gotcha |
| `author_logic` | The `get_language_reference` → write/import → `build_project` → fix-from-output loop (per language) |
| `scaffold_project` | `new_project` (exact CPU+version) → rack/PSU/IO → build → save |
| `add_modbus_device` | Add a Modbus-TCP slave DTM under the M580 CPU and configure a scan line |

## Extending the server

When you add a tool or capability, **also add (or extend) an MCP prompt** in [prompts.py](src/control_expert_mcp/prompts.py) that walks a client through using it. This is a project convention, not an afterthought: a good prompt turns an hour of trial-and-error into one slash command.

Write a prompt for anything a client **cannot guess from the tool description alone** — an environment flag (`CE_MCP_ENABLE_ONLINE`), a manual GUI step (the simulator family-check seed), an ordering constraint (stop the PLC before transfer), or an addressing rule (located-only Modbus, low-word-first REALs). Rules of thumb:

- Each new workflow → a prompt; each new tool → at least a mention in a relevant prompt.
- Put the **gotcha** in the prompt text explicitly (the steps that cost *you* time while building it).
- Mirror the validated sequence (tool names + argument shapes), and reference related prompts by name.
- Keep the standing orientation in the server `instructions` string short; put step-by-step recipes in prompts.

## Troubleshooting

- **`Catalog object not found` on `new_project`** — the CPU part number/version must exactly match the hardware catalog of *your* Control Expert version (spacing matters: `BMX P34 2020`, firmware like `02.70` is required).
- **`application object reference is not found in the catalog` on `add_io_module`** — same rule for modules: the catalog version is mandatory (`02.00` for most M340 IO modules, `01.00` for racks/power supplies).
- **`new_project` fails with a bare `Exception occurred` for a CPU family** — your Control Expert license/DTM library probably doesn't include that platform. The error carries no description; test the same CPU in the Control Expert GUI to confirm.
- **`add_dtm` says "protocol Id parameter is empty or invalid" / "Impossible to create a new DTM object"** — slave DTMs need the FDT protocol name as protocol_id (`Modbus` for the generic Modbus TCP device; the server auto-tries `Modbus` and `EtherNet/IP` when empty) and a device_type_name that exactly matches the DTM catalog (`Modbus Device`). List the catalog names with `tools/list_dtm_catalog.py`.
- **`set_master_dtm_dataset` crashes the server (RPC failed)** — almost always malformed request XML; in particular `requestUniqueID` must be a fresh GUID, not an integer. Mirror the validated template in the tool description exactly.
- **`Write access mode is already reserved by another client`** — Control Expert (the GUI) or another automation client has the project open for writing. Close it there first. The server holds one write token per session by design.
- **Broker creation fails** — Control Expert isn't installed, or its COM registration is broken (re-register by repairing the installation).
- **First call is slow** — `open_project`/`new_project` start the Unity server process and can take 30–120 s for large projects. Configure generous tool timeouts in your client.
- **Import fails with `Invalid file`** — the XML doesn't match the exchange schema. Export a similar object first (`read_section`, `export_xml`) and mirror its structure, including `fileHeader`/`contentHeader`.

## Architecture notes

- All COM calls run on a **single dedicated STA worker thread** (COM apartment affinity); MCP tool calls are marshalled onto it (the dispatcher is reentrant — nested bridge calls on the worker thread execute directly).
- Hardware and DTM objects expose their members on **secondary dual interfaces** (`IProject3`, `IConfiguration2`, `IBus`, `IModule`, `IPServerDtm*`, ...) that the default dispinterface doesn't include. The bridge QIs each object with IIDs harvested from `HKCR\Interface` and wraps the result as IDispatch (`_qi`), which is the only way to reach `DTMRoot`, `InternalBuses`, `AddChild`, etc. from late-bound clients.
- The **write-access token** (`app.Project(1)`) is acquired once per session and cached; failed-call tracebacks are stripped so they can't pin COM references and deadlock `ProjectClose`.
- `Project` is a parameterized COM property — it is invoked with explicit `DISPATCH_PROPERTYGET` flags because pywin32 dynamic dispatch can't call it.
- Import temp files get the **extension matching the XML root element** (`STExchangeFile` → `.xst`, `VariablesExchangeFile` → `.xsy`, ...) because Control Expert picks the parser from the extension.
- Enum constants (languages, export options, PLC commands...) were extracted from `PServer.tlb` — see [src/control_expert_mcp/constants.py](src/control_expert_mcp/constants.py).

## Disclaimer

Not affiliated with Schneider Electric. The UDE automation interface is provided by Schneider "as is" and is no longer commercialized; this project drives it at your own risk. **Never point online tools at production equipment without qualified supervision.**
