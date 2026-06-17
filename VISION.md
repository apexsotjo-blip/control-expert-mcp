# Vision & Working Guide — control-expert-mcp

> Internal document for the maintainer and any AI agent helping improve this server.
> Not user-facing docs (that's `README.md`). Read this first before making changes.
> Last updated: 2026-06-17.

## Vision

Make **EcoStruxure Control Expert fully programmable by AI agents over MCP** — so an AI
client can **create, read, test, and operate** PLC projects (M340 / M580 / Premium /
Quantum) end-to-end, using the **full capability** of Control Expert, **without needing
reference projects or human hand-holding**.

The server should be strong enough that a client starting from `new_project` (nothing on
disk) can author correct, advanced logic in any language, wire hardware and DTMs, run it on
the simulator, and read/write live values — guided the whole way.

## Goals (what "strong" means here)

1. **Full-coverage authoring** of all five IEC languages — not just the basics:
   - LD: every contact/coil type, `control` (jump/ret), `compareBlock`, `operateBlock`, inline `FFBBlock`, labels.
   - FBD: blocks + links, **comment boxes**, jumps/labels/returns, named connectors.
   - SFC: alternative *and* parallel branches, macro steps, multi-action steps, all qualifiers.
   - DFB/DDT definitions; ST (done) and IL.
2. **Clean DTM / Modbus builder** — add devices and scan lines from intent, not hand-written XML.
3. **Live testing loop** — run on the simulator, read/write live values, watch in a live CE UI.
4. **Self-sufficiency** — the server carries validated templates + generators + reference
   knowledge, so authoring never depends on an external sample `.stu`.
5. **Ease of use for any MCP client** (Claude, Codex, Copilot) — guided prompts + learning
   resources make a client productive immediately.

## Method / principles

1. **Control Expert is the oracle.** Every authored artifact is validated by a real
   `build_project` (reaching `built_ok`) before it is trusted or shipped. No "looks right."
2. **Grammar-grounded, not guessed.** Author from the authoritative schemas
   (`<install>\SrcXmlSchema\*.xsd`) and the UDE XML specs — then validate against the XSD,
   then build. Trial-and-error confirms a candidate; it never reveals the full element set.
3. **Three MCP primitives, used for what they're best at:**
   - **Tools** = actions (`write_st_logic`, `build_project`, `read_tags`).
   - **Prompts** = guided workflows (`commission_simulator`, `test_logic_live`).
   - **Resources** = knowledge the AI reads to learn (language catalogs, gotchas).
   - **Generators** (tools that emit validated XML) so the AI never hand-copies schema.
4. **Ship the knowledge with the feature.** Every new tool/capability lands with a prompt
   and, where relevant, a reference/gotcha entry that saves clients time. Capture a
   difficulty the moment you hit it — if it would bite the next client, it goes in.
5. **Safety.** Online/live tools are gated behind `CE_MCP_ENABLE_ONLINE`. Transfer/run/stop
   and live writes are flagged dangerous. Never point live tools at production without
   qualified supervision. For tests, use a throwaway `new_project` and never `save_project`
   over a real file.

## Non-negotiable technical truths (don't re-derive these)

- **No live values over COM.** The UDE/COM API is a *project-database* server (like
  Word/Excel for projects). Animation tables are display-only via COM; `IVariable` exposes
  only the offline initial value. **Live read/write = Modbus TCP** (`read_tags`/`write_tags`).
- **Modbus reaches LOCATED tags only** (`%M`/`%MW`). Unlocated DFB internals (e.g.
  `Pump1.Running`) must be **mirrored to `%MW`/`%M`** in the program to be visible.
  32-bit `REAL`/`DINT` are **Schneider low-word-first** by default.
- **The simulator exposes a Modbus server on `127.0.0.1:502`** (the endpoint a Vijeo I/O
  scanner reaches) — the live loop works against the sim, no hardware needed.
- **Simulator first transfer must be seeded manually in the CE GUI** (PLC → Simulation Mode
  → Connect → Transfer). The API download needs the sim to already hold a station of a
  matching CPU family ("Family check failed" otherwise). This includes **HSBY** CPUs — they
  CAN be simulated once seeded.
- **One write token at a time.** The GUI and the automation client cannot both hold write;
  close one before the other needs it.
- **Exchange XML is schema-strict.** Control Expert picks the import parser from the file
  **extension** (root element → suffix). Authoritative schemas: `<install>\SrcXmlSchema\`
  (`LDSource.xsd`, `FBDSource.xsd`, `SFCSource.xsd`, `commonElements.xsd`,
  `LLCommonElements.xsd`, `FDTDTMExchangeFile.xsd`, `IOConf.xsd`, …); `DTDVersion="41"`.
- **Graphical geometry rules:** LD — every `typeLine` must account for **all `nbColumns`**
  cells (default 11; cells under an FFB count as empty). FBD — block `height = 4 + visible
  input pins`; pin row = `posY + 4 + i`; link endpoints must equal the pin grid position or
  import fails (E1189); `textBox` uses a finer pixel-ish coordinate scale than the block
  grid. SFC — steps on even `posY`, transitions on odd.
- **Edge elements need `EBOOL`:** `PContact`/`NContact`/`PCoil`/`NCoil` require the variable
  to be `EBOOL` (edge memory), not `BOOL`.

## Current state (2026-06-16)

- **Live values**: `modbus_connect/disconnect/status`, `read_tags`, `write_tags` — validated
  end-to-end against the running sim (read 4.0 → write 3.5 → restore 4.0).
- **Live UI**: `show_ui` (read-only mode) + `open_animation_table`.
- **Prompts**: `commission_simulator`, `test_logic_live`, `author_logic`, `scaffold_project`,
  `add_modbus_device`.
- **`validate_xml`** (new `schema.py`, `lxml`): pre-validates exchange XML against the
  install's `SrcXmlSchema` before import/build. Folded into the `author_logic` prompt.
- **Advanced Ladder — COMPLETE & build-validated**, promoted into `lang_reference.py`
  (guide corrected + comprehensive example) and end-to-end proven (a fresh client authored
  NEW advanced LD → validate_xml → built_ok). Covers: 4 contact types (P/N edge → EBOOL),
  full coil family, `control` jump/ret + `labelCell`, `compareBlock`/`operateBlock`,
  parallel (OR) branch, cell-accounting law. Demo saved: `complex_ladder_demo.stu`.
  - **FFB block inside LD**: pin columns can't be hand-placed (CE owns the layout), BUT the
    rule was cracked and is now generated automatically by **`place_fb_in_ladder`** (NO
    template): reads a project DFB's interface, sizes the block (height = max(in,out)+1),
    wires ONE boolean input on its pin-row to the rail (EN left enabled), binds the rest,
    auto-declares vars. Build-validated for any DFB (e.g. FC_Valve). For elementary EFBs
    (TON/CTU) author in FBD; `use_fb_in_ladder` (clone a GUI template) remains as a fallback.
    Geometry rule: block spans `max(#in,#out)+1` rows; pin i is on row posY+i at the block
    edge; CE honours the descriptionFFB pin order, so the wired input's row = its index.
- **`use_fb_in_ladder`**: clone a GUI-made block-in-LD template + rebind vars (fallback for
  EFBs / exact GUI geometry). **`fb_in_ladder` prompt** documents the routes.
- **FBD**: example now ships `<textBox>` comments; guide documents jumps/labels/returns +
  named connectors.
- **Research done**: authoritative element catalog from the XSDs + semantics from the UDE
  XML specs (extracted text in `_docspec/`; LD samples in `_ld_samples/` — both temporary).
- **Tool count: 67** (verified via stdio client with CE_MCP_ENABLE_ONLINE=1).

### Commit status
- **v0.9.0** (branch `feat/ld-authoring-validate-and-usability-fixes`): the LD-authoring +
  `validate_xml` + DTM-builder batch on top of the earlier Modbus/live-UI/prompts release —
  `schema.py`, `validate_xml`, LD reference rewrite + FBD comments (`lang_reference.py`),
  `use_fb_in_ladder` + `place_fb_in_ladder` (`bridge.py`/`server.py`), prompt edits
  (`prompts.py`), `lxml` dep, `VISION.md`, `README.md`.
- Same release folds in **client-usability fixes** found by a black-box MCP-client test:
  transactional ZEF reload so a rejected DTM-dataset/`_do_zef_patch` write can't strand the
  project (`_reimport_zef`), `get_dtm_dataset` UTF-16/binary-framing decode + honest "not
  round-trippable" note, actionable errors for `remove_io_module`/`replace_io_module` on
  remote EIO racks (headless API rejects `IModules.DeleteChild` there), and the server now
  reports its real `version` to clients.
- Known follow-ups (from the same test, not yet done): apply the large-export temp-file
  fallback to the other XML getters (`get_master_dtm_dataset`, `get_dtm_control_parameters`,
  `read_section`); remote-rack PSU auto-sizing; confirm whether any headless path exists for
  EIO module delete.

### Pending / in flight
- **FINAL BIG TEST not yet run**: a copy `DR001_complex_demo.stu` was made (FAT untouched);
  the plan is to `open_project` it and `place_fb_in_ladder` for **Pump1 (FC_VSD)** + **AV1/AV2
  (FC_Valve)**, add an all-elements LD section (use `DEMO_`-prefixed vars to avoid clashing
  with DR001's 437 vars), `build_project` → `built_ok`, `save_project`. Run this to close the
  Ladder chapter, then move on.

## Roadmap (next)

0. **Run the final big test** (above) and **commit the uncommitted batch**.
1. **Advanced SFC**: mine the spec's `linkSFC` directed-link pin-coordinate rules (in
   `_docspec/UDEXMLUnityProIECLanguages.txt`), then build-validate alternative + parallel
   branches, macro steps, multi-action qualifiers; upgrade the SFC reference/example.
2. **Per-language element-catalog resource** (`@mcp.resource`) + a `read_reference` tool
   fallback (clients that don't read MCP resources).
3. **Generators** — emit validated XML from intent: FBD `call_block` (any block, deterministic
   FBD geometry), SFC chart, `create_dfb`, `add_modbus_scan_line` (promote the template out of
   the `set_master_dtm_dataset` docstring).
4. **Gotchas/learning resource** — living doc; seed with: FFB-in-LD layout rule, `Calc` (and
   other reserved identifiers) → E1235, P/N contacts+coils need EBOOL, sim family-check seed,
   Modbus located-only/low-word-first, single write token.
5. **Known bug**: `create_task` (e.g. FAST) returns "Bad parameter (0x80020009)" — fix.

## Repo map (where to work)

- `src/control_expert_mcp/bridge.py` — all COM access (single STA worker thread); the hard part.
- `src/control_expert_mcp/server.py` — MCP tool/prompt registration; `instructions` string.
- `src/control_expert_mcp/modbus.py` — live-value Modbus client + IEC codec.
- `src/control_expert_mcp/prompts.py` — guided-workflow prompts (add one per feature).
- `src/control_expert_mcp/lang_reference.py` — embedded authoring guides + validated examples.
- `src/control_expert_mcp/constants.py` — enums harvested from the type library.
- `tools/validate_templates.py` — the synthesize → import → build harness (the bootstrap engine).
- `<install>\SrcXmlSchema\` — authoritative exchange-format XSDs (the grammar).
- `_docspec/` — extracted UDE XML spec text (temporary; fold useful parts into resources).
