# DDT Mirror

One-click linking of EcoStruxure Control Expert tags to the rest of the
project — Vijeo Designer HMIs and SCADAPack RTUs (RemoteConnect).

**Flow:** open → pick types → pick members / Read-Write access → **choose a
destination** → the app assigns the addressing *for that destination* and
you generate. Selection is destination-agnostic and address-free; addresses
are never assigned before you pick where the tags go. This matters because
the two destinations use unrelated addressing — PLC `%M`/`%MW` vs RTU DNP3
points + Modbus registers — and the RTU addresses can only be decided
*after* you supply your RemoteConnect export (they are packed above whatever
that workbook already uses).

## PLC-internal mirror → Vijeo / Modbus HMI

The problem: unlocated DDT members are unreachable over Modbus, so HMI linking
normally means hand-creating located mirror tags, hand-writing an ST mirror
section (with the copy on the correct side of `:=` per tag, or HMI writes get
clobbered), and retyping every tag in Vijeo. Vijeo's own Unity-link (XVM/STU)
maps unlocated members to `%UV...` internal references that break on every PLC
build. This app automates the whole located-mirror approach instead:

1. **Open** your `.stu` — the app extracts every variable and DDT definition.
2. **Pick types** to share (DDT types and elementary BOOL/INT/REAL/... tags).
3. **Pick members** (all preselected) and set per-tag HMI access:
   *Read* (display) or *Read/Write* (commands/setpoints) — preset from naming
   (`Cmd*`, `Man*`, `*_SP` → Read/Write), editable per DDT type. **Save R/W
   defaults for these DDTs** stores the member selection and access, keyed by
   DDT type name, in a global library (`%APPDATA%\DdtMirror\ddt_library.json`,
   override with `DDT_MIRROR_LIBRARY`). Any project opened afterwards whose
   DDT type names match is configured from it automatically — so a DDT's R/W
   scheme is defined once, not per project. The library is authoritative for
   the types it covers (it overwrites those types' member config on open);
   it only touches type-level config, never per-variable overrides,
   standalone tags or address allocation.
4. **Choose the PLC-internal mirror destination.** Only now are `%M`/`%MW`
   assigned — you review the ST section, new variables and address map.
5. **Generate**: located mirror variables are created (REAL/32-bit types only —
   BOOL/INT mirror directly to `%M`/`%MW` literals), the `HMI_MIRROR` ST
   section is written into MAST, the project is rebuilt to prove correctness
   and saved, and an address-map CSV is emitted.
6. **Export Vijeo variables (XML)** and import via the Variables node in
   Vijeo Designer — tags arrive named, typed, and addressed.

## Mirroring scheme

| CE type | Address | ST copy | Vijeo import |
|---|---|---|---|
| BOOL/EBOOL | `%M` coil | direct literal | `BOOL` at `%Mi` |
| INT/UINT/WORD | one `%MW` | direct literal | `INT`/`UINT` at `%MWi` |
| REAL | two `%MW` | via created located variable | `REAL` at `%MFi` |
| DINT/UDINT/DWORD/TIME | two `%MW` | via created located variable | `DINT`/`UDINT` at `%MDi` |

Read/Write copies (`Tag := addr`) run before Read copies (`addr := Tag`);
each address has exactly one writer, so HMI writes are never clobbered.
Arrays, STRING, DATE/TOD are not supported yet (skipped with warnings).

**Address stability**: allocations are persisted in a sidecar
(`<project>.hmimirror.json`, next to the `.stu`) and are append-only — 
regenerating never moves an existing address, so HMI screens keep working.
Deselected tags keep their (tombstoned) address and revive on reselect.

## SCADAPack RTU → RemoteConnect

SCADAPack x70 RTUs are programmed in the RemoteConnect Logic Editor (a
Control Expert-based IEC editor with no automation API). Engineers author in
Control Expert, then move the program into the Logic Editor. RTU objects
(the points SCADA/HMI sees) bind to logic variables **by name** — an object
with a `T_SPx70_*` Logic Variable Type appears as a variable of that name.

Flow (full-workbook round-trip, nothing in the RTU config is lost):

1. In RemoteConnect, export the RTU configuration to `.xls`.
2. In this app, after selecting tags, choose the **SCADAPack RTU**
   destination. Point it at that `.xls`, set the HMI address indexing
   (0-based default / 1-based, see below), then **Assign addresses &
   preview**: DNP3 points / registers are
   allocated against the workbook and the object/point/register map is shown
   for review *before anything is written*. **Generate transfer bundle**
   then writes:
   - `<project>_RTU_import.xls` — your full config + one object per
     selected leaf;
   - `<project>_RTU_mirror.st` — paste-ready Logic Editor mirror section;
   - `<project>_RTU_point_map.csv` — objects, DNP3 points, RTU registers
     and the HMI-side IEC address per tag;
   - `<project>_RTU_variables.xsy` — the CE variables export **without**
     the generated located mirror variables (M580 plumbing that would only
     pollute the Logic Editor);
   - `<project>_RTU_sections\NN_task_section.xst/.xld/...` — every logic
     section as its own typed exchange file, **excluding** the generated
     `HMI_MIRROR` section (superseded by the mirror `.st`).
4. In RemoteConnect: import the `.xls`; in the Logic Editor import the
   `.xsy`, then the section files in numbered order, create a final ST
   section and paste the mirror `.st`, build.

**HMI address indexing (0/1, default 0):** the point map's `HmiAddress`
column gives the IEC address an HMI polling the RTU should use:
`%MW(register − 40001 + base)` / `%M(coil − 1 + base)`. Base 0 is the
Modbus standard (register 40001 = wire address 0 = `%MW0`); base 1 suits
drivers/servers that map 40001 to `%MW1`.

### Shelved: generating a Logic Editor project (.stu) directly

An earlier feature drove the installed Logic Editor (an OEM Control Expert
on the same COM broker, selected via the SCADAPack x70 DTM's xpdf context)
to produce a ready-to-open native `.stu` with variables, sections and the
mirror already inside — proven end-to-end locally (`SCADAPack x70 Logic
11.10`, level 93). It was **removed from the UI/transfer flow until it is
production-ready**; the mechanism is preserved in
[logic_project.py](src/ddt_mirror/codegen/logic_project.py) (see its
docstring for the revive checklist) and
`bridge.new_logic_editor_project` in the parent repo.

**Duplicate addressing:** allocation always stays clear of every DNP3
point and Modbus register already present in the source workbook. If an
engineer meanwhile hand-created objects on numbers a previous run had
assigned, ours are renumbered when they never landed in the RTU, or
reported as live duplicates to resolve in RemoteConnect when both exist;
duplicates already present inside the source itself are reported too.

Mapping (enums copied from a real RemoteConnect v3.0 / SCADAPack 474
export): BOOL→Digital `T_SPx70_BOOL` (DNP3 g1v1, Modbus coil);
INT→Analog `T_SPx70_INT` (g30v1 read / g40v1 write, holding register);
DINT/REAL→Analog `T_SPx70_DINT`/`T_SPx70_REAL`; UDINT→Counter
`T_SPx70_UDINT` (g20v1); UINT/WORD→`T_SPx70_INT` with `*_TO_*` conversion
copies. `T_SPx70_*` object variables are **structures** — the process
value is their `.value` member, and every copy line reads/writes it
(`Pump1_Flow_PV.value := Pump1.Flow_PV;`). Because the imported object
auto-creates a Logic Editor variable of its own name, object names must
never collide with program variables: DDT members get
`Pump1_Ctrl_Man_SP`-style names and standalone tags get a `_Obj` suffix. DNP3 point numbers (per group) and RTU Modbus server registers
(coils / 4xxxx) are allocated append-only above everything already in the
workbook, and persist in the same sidecar — regeneration never renumbers.

**Point-collision rule (import-probed on a SCADAPack 474):** RemoteConnect
auto-assigns internal DNP3 point numbers to objects whose export shows an
empty point cell, and silently drops imported rows whose explicit point
collides — so every new point is numbered above the workbook's total
object count. Points from a rejected import (object absent from the
workbook) are renumbered automatically on the next export.

## Vijeo side (one-time equipment setup)

- IO Manager → ModbusTCPIP equipment → enable **IEC61131 syntax**,
  set Double Word word order to **Low word first** (Schneider default).
- Import the exported XML: Variables node → **Import Variables** → Unicode XML.
- The scan-group name you enter at export must match the equipment's scan
  group (or map it in the import dialog).

## Running

```
.venv\Scripts\python -m ddt_mirror.gui.app
```

Requires: Windows with Control Expert installed, plus this repo's venv
(`pip install -e <parent control-expert-mcp repo>` and `pip install -e .`).

## Development

- Core engine is COM-free and unit-tested: `pytest tests` (78 tests); the
  headless GUI smoke (`python tools/smoke_gui.py`) drives the full page flow.
- `tools/verify_e2e.py [--live]` — end-to-end against real Control Expert:
  scratch project + test DDT, generate, `built_ok`, rerun-stability; `--live`
  adds simulator transfer + a Modbus round-trip proving both copy directions
  (fresh simulators need one manual GUI transfer first to seed the CPU family).
- `tools/smoke_gui.py` — headless GUI flow test (fixture data, no COM).
- `tools/smoke_gui_live.py` — GUI worker path against the e2e scratch project.

Format sources (verified, not guessed): Vijeo `public.xsd`
(`Vijeo-Frame\XML\`), `SEI_ModbusTCPIP_E.pdf` device-address chapter, and the
`InputAddress` help topics; CE exchange grammar from `SrcXmlSchema\*.xsd`;
RemoteConnect workbook layout and every object-row enum from a real v3.0
export (`remoteconnect.xls`, SCADAPack 474, DTM 4.6).

Known RemoteConnect unknowns (need a probe): Binary Output DNP3 group enum
for SCADA-written digitals. Probed and confirmed: values-only workbooks
import; explicit points/registers on logic-bound objects work (with the
point floor); 5,456 objects in one import (7,761 total on a 474); RC
writes `*_ImportLog.txt` (UTF-16) next to the imported file; `T_SPx70_*`
Logic Editor variables are structs whose scalar is `.value` (so direct
name-binding of program variables is impossible — everything is copied).
