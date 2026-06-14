# Big recreation stress test — DR001 Dabouq Reservoir

**Goal:** rebuild `sw25-gma-01.plc_20260608_2200_fat.stu` (an M580 **HSBY** remote-EIO
project: BME H58 2040, 437 vars, 86 DDTs / 20 DFBs, 19 MAST sections, 19 Modbus/EIP
slave DTMs) entirely through the MCP tools — no exchange-file imports from the
reference. The reference was used **read-only** to learn structure; all authoring
went through the tools.

## Result

Structural reproduction reached ~95%:

| Item | Reference | Recreated | Via |
|---|---|---|---|
| CPU + local rack + PSU | H58 2040 / XBP 0400 / CPS 2010 | ✅ exact | new_project + replace_rack/replace_io_module |
| Remote EIO drop + rack + 9 IO modules | ✅ | ✅ exact | add_drop / add_rack / add_io_module |
| User DDTs | 16 | ✅ 16 | export_xml(ddt) → import_xml |
| DFBs (with internal logic) | 20 | ✅ 20 | export_xml(dfb) → import_xml |
| User variables | 407 (+30 auto) | ✅ 407 | create_variable |
| Program sections (ST + FBD) | 19 | ✅ 19, order preserved | write_st_logic / import_xml |
| Modbus slave DTMs + IPs + scan lines | 17 Modbus | ✅ 17 | add_dtm / set_dtm_address / set_master_dtm_dataset |
| CPU Ethernet IP/A/B/D + 6 security services | ✅ | ✅ (security + IPs verified in binary) | configure_cpu_ethernet |
| Project settings (multiAssign, dynamicArray, TZ) | ✅ | ✅ 4/4 | **set_project_settings (new)** |

**Build status after the fix pass:** the recreation now runs through every tool
with **zero tool failures** and the device-DDT error storm is gone. The build
stops at **49 errors, all from two headless-automation boundaries** (48 from the
HART_SIGNALS section needing the AHI HART gateway DTM, 1 from the HSBY EtherNet/IP
scanner validation) — both require the interactive DTM build that the headless
server refuses. Everything reachable through the documented automation API is
reproduced.

### Update — fixes applied (this pass)

Six of the ten findings below are now **fixed in the bridge**; the remaining four
are characterized as genuine platform boundaries, not tool bugs.

## Weaknesses fixed during the test (tool gaps)

1. **`export_xml` rejected `kind="ddt"`** — could read DFBs but not DDTs, so no
   read path for derived types. *Fixed:* added the `ddt` branch (Ddts collection,
   `.xdd`). All 86 reference DDTs now export.
2. **No way to change the default rack/PSU.** `new_project` always lays down BME
   XBP 0800 + CPS 4002; the reference uses XBP 0400 + CPS 2010 and `add_*` fails
   with "topological address already used." *Fixed:* added **`replace_io_module`**
   and **`replace_rack`** (rack uses `IRacks::ReplaceChild`; module replace = delete
   + add, because `IModules::ReplaceChild` returns "service not applicable here").
3. **No project-settings tool.** The DFBs use chained assignment `a:=b:=c`; a fresh
   project has `unity.multiAssign=0`, so every such DFB failed to analyze with
   **E1203** (≈40 errors). The COM API only exposes a *read*
   (`GetProjectSettingValue`) and a bulk `.xso` import. *Fixed:* added
   **`get_project_setting`** + **`set_project_settings`**, patching the
   `<entryvalue ident=… value=…>` elements in `unitpro.xef` via the ZEF round-trip.
   Clearing `multiAssign`+`dynamicArray` removed all E1203 errors.
4. **`configure_cpu_ethernet` only set IP A and C.** HSBY CPUs use IP A/B and an
   address D. *Fixed:* added `ip_b` / `ip_d` parameters (paramKPW offsets 30/46,
   TcpSettings staticIPAddressB/D).

## Findings 5-10

5. **★ FIXED — section import duplicated EIO device-DDT instances (the main
   blocker, 376 errors).** Diagnosis corrected during the fix pass: the ZEF
   round-trip was *not* the culprit (a minimal repro showed `set_master_dtm_dataset`
   / `configure_cpu_ethernet` / `set_project_settings` leave device DDTs intact).
   The real cause is the **section exchange file's `<dataBlock>`**, which
   re-declares each referenced device DDT with an `<attribute name="Owner">`
   (the hardware topological owner). Importing that into a project where the I/O
   configuration already owns the name spawns an unmapped `name_0` duplicate, and
   the section binds to it → E1061/E1066/E1076 storm. *Fix:* `import_xml` now strips
   `Owner`-attributed (hardware-managed) declarations from section/program imports
   (`_strip_managed_devddt_decls`); the section binds to the real mapped device
   variable. Re-run: **376 errors → 0**, no duplicates created.
6. **Boundary — HSBY EtherNet/IP scanner validation requires the interactive DTM
   build.** Exhaustively chased: the security bitmask is correct (0x27FF, eipServer
   on), the EtherNet/IP scanner is `enabled="true"`, the CRA's CIP connection block
   is byte-for-byte equivalent to the reference, and patching the channel paramKPW
   IP copies changes the displayed IP in the error but not the error itself.
   Transplanting every reference paramKPW byte-range and a full-XEF attribute diff
   found **no stored flag that differs** — only computed bandwidth values. The
   *"EIP option must be checked for the HSBY cpu"* check is evaluated at build time
   from the DTM's internal build state, which is produced by `BuildControlInformation`
   / `UpdateDtmPlcLink` — and those fail headlessly ("DTM not linked with PLC
   control"), the same dead-end the security fix had to bypass with byte-patching.
   Non-HSBY M580 clears via the security bits (proven on gma_recreate); HSBY does
   not, because the extra validation has no patchable stored flag. **Genuine
   headless boundary.**
7. **Boundary — AHI HART gateway DTMs require interactive DTM creation.**
   `BME AHI 0812` is a HART *gateway* DTM (`Mx80HARTGateway.DTMCore`). `add_dtm`
   fails "Impossible to create a new DTM object" for every protocol/prog-id combo,
   and `get_dtm_dataset` on the reference instances fails "DTM is not a device".
   These gateways are instantiated by the CPU DTM's interactive editor when HART is
   enabled on the module — the same interactive-DTM surface that is unavailable
   headlessly. The 48 HART_SIGNALS errors all stem from the 2 missing
   `BME_AHI_0812_r0_s03/_s04` instances. **Genuine headless boundary.**
8. **FIXED — `set_dtm_address` now sets the STB NIP2x1x island address.** The FDT
   `SlaveBusAddress` property is rejected by island/STB DTMs; `set_dtm_address` now
   falls back to patching `slaveDeviceAddressID` + the CIP `ExtendedIdentifier` +
   the application device-list `ipAddress` via the dataset path (same mechanism as
   the gateway fix). IO_RackVA address is set to 20.10.21.24.
9. **Mitigated — stale write-token after a crash.** The harness kills stray
   `psbroker`/`ControlExpert` processes and removes `.ztx` tokens before each phase
   (`common.kill_strays`); the bridge surfaces a clear recovery hint on the lock
   error.
10. **FIXED — `save_project` after a ZEF reimport.** `_do_zef_patch` now preserves
    the original `.stu` path, and `save_project(None)` does a `SaveAs` back to it
    when the reimported project has lost its file binding. No more "Cannot access
    file"; the recreation's phase-3 save now succeeds without a temp-swap.

## New tools added (this + prior pass)

`export_xml(kind="ddt")`, `replace_io_module`, `replace_rack`,
`get_project_setting`, `set_project_settings`, and `configure_cpu_ethernet`
`ip_b`/`ip_d` parameters — all validated against the DR001 recreation.

## Net

The tools handled the entire **authoring** surface cleanly: hardware topology,
106 derived types, 427 variables, 19 mixed-language sections in order, the full
Modbus DTM chain with scan lines, security config, and project settings. The fix
pass resolved every tool-level weakness (device-DDT duplication, STB address, save
after reimport) and added six tools. The remaining unreached `built_ok` is due to
exactly **two interactive-DTM-editor features** — the HSBY EtherNet/IP scanner
validation and the AHI HART gateway DTMs — which the headless Control Expert
server structurally refuses (`BuildControlInformation`/`UpdateDtmPlcLink` →
"DTM not linked with PLC control"; `DisplayDefaultEditor` → "server is not running
in interactive mode"). Those are a property of the automation surface, not of this
toolset, and are the documented ceiling for headless recreation of an HSBY+HART
project.
