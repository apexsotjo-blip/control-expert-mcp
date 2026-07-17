"""FastAPI backend: a thin, stateful shell over the ddt_mirror engine.

One project session per server process (matching the one-broker/one-app
COM reality). The ControlExpertBridge serializes all COM work on its own
STA thread, so calling it from request threads is safe; a lock keeps
engine state consistent.

Selection model over the wire: the client edits a full snapshot
(selected types, per-leaf checked flags, access override keys) and POSTs
it back; the server classifies it into the sidecar's per-variable vs
type-level exclusions with the same rule the desktop tree uses (a member
unchecked across EVERY instance of its type becomes a type-level
exclusion, so future instances inherit it).
"""

from __future__ import annotations

import datetime as _dt
import os
import threading
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import __version__
from ..core.adopt import load_or_recover_sidecar
from ..core.engine import apply_plan, build_plan, scan_project, type_summary

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


class _Session:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.bridge = None
        self.data = None            # ProjectData
        self.state = None           # SidecarState
        self.project_path = ""
        self.plan = None
        self.new_alloc = None
        self.t2 = None              # (plc_plan, plc_alloc) staged for T2
        self.activity: list[dict] = []

    def log(self, text: str, kind: str = "info") -> None:
        self.activity.append({
            "time": _dt.datetime.now().strftime("%H:%M:%S"),
            "kind": kind,
            "text": text,
        })
        del self.activity[:-400]

    def get_bridge(self):
        if self.bridge is None:
            self.log("Starting Control Expert automation session...")
            from control_expert_mcp.bridge import ControlExpertBridge

            self.bridge = ControlExpertBridge()
        return self.bridge

    def require_project(self) -> None:
        if self.data is None:
            raise HTTPException(409, "No project open.")


S = _Session()
app = FastAPI(title="DDT Mirror", version=__version__)


# ------------------------------------------------------------------ models

class OpenReq(BaseModel):
    path: str


class SelectionSnapshot(BaseModel):
    selected_types: list[str]
    unchecked: list[str]                 # full_paths currently unchecked
    access_overrides: dict[str, str]     # replaces the sidecar's dict


class RtuAssignReq(BaseModel):
    src_xls: str
    mode: str = "logic"                  # "logic" | "scanner"
    device: str = ""
    hmi_index_base: int = 0


class GenerateRtuReq(RtuAssignReq):
    out_dir: str = ""


# ------------------------------------------------------------------- meta

@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/api/status")
def status() -> dict:
    with S.lock:
        return {
            "version": __version__,
            "project": os.path.basename(S.project_path) or None,
            "project_path": S.project_path or None,
            "leaves": len(S.data.leaves) if S.data else 0,
        }


@app.get("/api/activity")
def activity(after: int = 0) -> dict:
    return {"items": S.activity[after:], "next": len(S.activity)}


# ---------------------------------------------------------------- project

@app.post("/api/open")
def open_project(req: OpenReq) -> dict:
    path = req.path.strip().strip('"')
    if not os.path.isfile(path):
        raise HTTPException(400, f"Not a file: {path}")
    with S.lock:
        bridge = S.get_bridge()
        S.log(f"Opening {path} (first open can take a minute)...")
        bridge.open_project(path)
        S.log("Reading variables and DDT definitions...")
        S.data = scan_project(bridge)
        S.project_path = path
        S.state, recovery = load_or_recover_sidecar(path, S.data)
        from ..core.ddt_library import apply_library, load_library

        applied = apply_library(load_library(), S.data, S.state)
        S.plan = S.new_alloc = S.t2 = None
        S.log(f"Opened {os.path.basename(path)}: {len(S.data.leaves)} "
              f"mirrorable tags, {len(S.data.types)} DDT types.", "ok")
        if applied:
            S.log("Applied saved R/W defaults for: " + ", ".join(applied))
        for line in recovery:
            S.log(line, "warn")
        return {"ok": True, "recovered": bool(recovery)}


@app.get("/api/project")
def project() -> dict:
    with S.lock:
        S.require_project()
        d, st = S.data, S.state
        r = d.reserved
        return {
            "name": os.path.basename(S.project_path),
            "path": S.project_path,
            "leaves": len(d.leaves),
            "ddt_types": len(d.types),
            "reserved": {
                "max_bit": r.max_bit, "max_word": r.max_word,
                "located": r.n_located, "literals": r.n_literals,
            },
            "generated_sections": [s.name for s in d.generated_sections],
            "alloc_count": len(st.alloc.leaves),
            "rtu_count": len(st.rtu.entries),
            "warnings": d.warnings,
            "types": type_summary(d),
        }


@app.get("/api/variables")
def variables() -> dict:
    """Everything the variables table needs, with current selection."""
    with S.lock:
        S.require_project()
        st = S.data, S.state
        data, state = st
        unchecked_vars = set(state.deselected_leaves)
        excluded_members = set(state.deselected_type_members)
        from ..core.access import guess_access

        rows = []
        for leaf in data.leaves:
            override = (state.access_overrides.get(leaf.instance_access_key)
                        or state.access_overrides.get(leaf.access_key))
            access = override or guess_access(
                leaf.rel_path or leaf.instance).value
            checked = not (
                leaf.full_path in unchecked_vars
                or (leaf.ddt_type and leaf.access_key in excluded_members))
            rows.append({
                "path": leaf.full_path,
                "instance": leaf.instance,
                "member": leaf.rel_path,
                "ddt_type": leaf.ddt_type,
                "group": leaf.ddt_type or leaf.type_name,
                "type": leaf.type_name,
                "kind": leaf.kind.value,
                "comment": leaf.comment,
                "located": leaf.located,
                "access": access,
                "checked": checked,
                "type_key": leaf.access_key,
                "var_key": leaf.instance_access_key,
            })
        return {
            "selected_types": state.selected_types,
            "access_overrides": state.access_overrides,
            "rows": rows,
        }


@app.post("/api/selection")
def set_selection(snap: SelectionSnapshot) -> dict:
    """Persist a full selection snapshot into the sidecar lists."""
    with S.lock:
        S.require_project()
        data, state = S.data, S.state
        state.selected_types = snap.selected_types
        state.access_overrides = dict(snap.access_overrides)
        unchecked = set(snap.unchecked)
        # classify: unchecked on every instance of a DDT type -> type-level
        totals: dict[str, list[int]] = {}
        for leaf in data.leaves:
            if not leaf.ddt_type:
                continue
            t = totals.setdefault(leaf.access_key, [0, 0])
            t[0] += 1
            if leaf.full_path in unchecked:
                t[1] += 1
        type_members = sorted(
            k for k, (total, off) in totals.items() if off and off == total)
        type_set = set(type_members)
        per_var = sorted(
            p for p in unchecked
            if not any(l.access_key in type_set for l in data.leaves
                       if l.full_path == p))
        state.deselected_type_members = type_members
        state.deselected_leaves = per_var
        S.plan = S.t2 = None  # selection changed: staged plans are stale
        return {"per_var": len(per_var), "type_level": len(type_members)}


# -------------------------------------------------------------- PLC mirror

@app.post("/api/plc/preview")
def plc_preview() -> dict:
    with S.lock:
        S.require_project()
        S.plan, S.new_alloc = build_plan(
            S.data, S.state,
            project_name=os.path.basename(S.project_path))
        S.log(f"PLC preview: {len(S.plan.new_variables)} mirror variables, "
              f"{sum(1 for a in S.plan.assignments if not a.premapped)} "
              "copies.")
        return {
            "st": S.plan.st_source,
            "csv": S.plan.csv_text,
            "new_variables": S.plan.new_variables,
            "warnings": S.plan.warnings,
        }


@app.post("/api/plc/generate")
def plc_generate() -> dict:
    with S.lock:
        S.require_project()
        if S.plan is None:
            raise HTTPException(409, "Run the preview first.")
        bridge = S.get_bridge()
        S.log("Generating into the project (create/write/build/save)...")
        report = apply_plan(bridge, S.plan, S.new_alloc, S.state,
                            S.project_path, progress=S.log)
        result = {
            "ok": report.ok,
            "created": report.created_vars,
            "skipped": report.skipped_vars,
            "build_state": report.build_state,
            "csv_path": report.csv_path,
            "sidecar_path": report.sidecar_path,
            "warnings": report.warnings,
            "error": report.error,
            "build_output": report.build_output if not report.ok else "",
        }
        S.log("Generate " + ("OK" if report.ok else f"FAILED: {report.error}"),
              "ok" if report.ok else "error")
        return result


class VijeoReq(BaseModel):
    scan_group: str
    out_dir: str = ""


@app.post("/api/vijeo/export")
def vijeo_export(req: VijeoReq) -> dict:
    from ..codegen.vijeo import generate_vijeo_files

    with S.lock:
        S.require_project()
        if S.plan is None:
            raise HTTPException(409, "Run the PLC preview first.")
        out_dir = req.out_dir.strip() or os.path.dirname(S.project_path)
        if not os.path.isdir(out_dir):
            raise HTTPException(400, f"Not a folder: {out_dir}")
        udt_text, csv_text, warnings = generate_vijeo_files(
            S.data.types, S.data.tags, S.plan, S.state,
            req.scan_group.strip() or "ModbusEquipment01")
        stem = os.path.splitext(os.path.basename(S.project_path))[0]
        udt_path = os.path.join(out_dir, f"{stem}_UDT.VJDDataTypes")
        csv_path = os.path.join(out_dir, f"{stem}_HMI_variables.CSV")
        with open(udt_path, "w", encoding="utf-8") as fh:
            fh.write(udt_text)
        with open(csv_path, "w", encoding="utf-8") as fh:
            fh.write(csv_text)
        S.log(f"Vijeo export: {udt_path} + {csv_path}", "ok")
        return {"udt_path": udt_path, "csv_path": csv_path,
                "warnings": warnings}


# ------------------------------------------------------------ SCADAPack RTU

@app.post("/api/rtu/assign")
def rtu_assign(req: RtuAssignReq) -> dict:
    with S.lock:
        S.require_project()
        if not os.path.isfile(req.src_xls):
            raise HTTPException(400, f"Not a file: {req.src_xls}")
        S.state.settings.hmi_index_base = req.hmi_index_base
        try:
            if req.mode == "scanner":
                from ..codegen.scanner import (
                    generate_t2_map_csv, plan_scanner,
                )

                if not req.device.strip():
                    raise HTTPException(400, "Enter the PLC's Modbus device "
                                             "name from RemoteConnect.")
                plc_plan, plc_alloc = build_plan(
                    S.data, S.state,
                    project_name=os.path.basename(S.project_path),
                    word_bools=True)
                t2 = plan_scanner(S.data, S.state, plc_plan.assignments,
                                  req.src_xls, req.device)
                S.t2 = (plc_plan, plc_alloc)
                S.log(f"RTU assign (scanner, device '{t2.device.name}'): "
                      f"{t2.created_objects} objects, "
                      f"{len(t2.new_blocks)} blocks, "
                      f"{len(t2.bind_rows)} bindings.")
                return {
                    "mode": "scanner",
                    "device": t2.device.name,
                    "created": t2.created_objects,
                    "existing": t2.existing_objects,
                    "blocks": len(t2.new_blocks),
                    "bindings": len(t2.bind_rows),
                    "plc_variables": len(plc_plan.new_variables),
                    "map_csv": generate_t2_map_csv(t2.map_rows),
                    "warnings": t2.warnings,
                }
            from ..codegen.remoteconnect import preview_remoteconnect

            pv = preview_remoteconnect(S.data, S.state, req.src_xls)
            S.log(f"RTU assign (Logic Editor): {pv.created} new objects.")
            return {
                "mode": "logic",
                "device_type": pv.device_type,
                "created": pv.created,
                "existing": pv.existing,
                "map_csv": pv.map_csv,
                "warnings": pv.warnings,
            }
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(400, str(exc))


@app.post("/api/rtu/generate")
def rtu_generate(req: GenerateRtuReq) -> dict:
    with S.lock:
        S.require_project()
        if not os.path.isfile(req.src_xls):
            raise HTTPException(400, f"Not a file: {req.src_xls}")
        out_dir = req.out_dir.strip() or os.path.dirname(S.project_path)
        if not os.path.isdir(out_dir):
            raise HTTPException(400, f"Not a folder: {out_dir}")
        S.state.settings.hmi_index_base = req.hmi_index_base
        bridge = S.get_bridge()
        if req.mode == "scanner":
            if S.t2 is None:
                raise HTTPException(409, "Run Assign first.")
            from ..codegen.scanner import export_scanner_bundle

            plc_plan, plc_alloc = S.t2
            S.log("T2: applying the PLC mirror (create/write/build/save)...")
            report = apply_plan(bridge, plc_plan, plc_alloc, S.state,
                                S.project_path, progress=S.log)
            if not report.ok:
                S.log(f"T2 FAILED at the PLC step: {report.error}", "error")
                return {"ok": False, "error": report.error,
                        "build_output": report.build_output}
            S.log("T2: enriching the RemoteConnect workbook...")
            t2 = export_scanner_bundle(
                S.data, S.state, plc_plan.assignments, req.src_xls, out_dir,
                S.project_path, req.device)
            S.log(f"T2 complete: {t2.xls_path}", "ok")
            return {
                "ok": True, "mode": "scanner",
                "files": [p for p in (t2.xls_path, t2.map_path,
                                      t2.geoscada_path) if p],
                "created": t2.created_objects, "blocks": t2.new_blocks,
                "bindings": t2.new_bindings,
                "plc": {"created": report.created_vars,
                        "build_state": report.build_state},
                "warnings": list(report.warnings) + list(t2.warnings),
            }
        from ..codegen.transfer import transfer_to_remoteconnect

        S.log("Generating the RemoteConnect transfer bundle...")
        report = transfer_to_remoteconnect(
            bridge, S.data, S.state, req.src_xls, out_dir, S.project_path,
            timestamp=_dt.datetime.now().isoformat(timespec="seconds"),
            progress=S.log)
        rc = report.rc
        S.log(f"Bundle complete: {rc.xls_path}", "ok")
        return {
            "ok": True, "mode": "logic",
            "files": [p for p in (
                rc.xls_path, rc.st_path, rc.map_path, report.xsy_path,
                report.sections_dir, rc.geoscada_path) if p],
            "created": rc.created, "existing": rc.existing,
            "warnings": report.warnings,
        }


app.mount("/static", StaticFiles(directory=STATIC), name="static")
