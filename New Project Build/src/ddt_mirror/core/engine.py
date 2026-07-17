"""Orchestrator: scan the project, build a mirror plan, apply it.

build_plan is pure (no COM) so the GUI can preview everything before
apply_plan touches the project. Allocation state is only persisted after a
successful build + save, so a failed run never burns addresses.
"""

from __future__ import annotations

import copy
import datetime as _dt
import os
from dataclasses import dataclass, field
from typing import Callable

from .. import __version__
from .access import apply_access
from .adopt import (
    MIRROR_COMMENT_PREFIX, GeneratedSection, ReservedUsage,
    find_generated_sections, scan_reserved,
)
from .allocator import AllocState, allocate
from .csvmap import generate_csv
from .flatten import flatten_tags
from .model import DdtType, FlatLeaf, MirrorPlan, Tag
from .persist import SidecarState, save_sidecar
from .stgen import CE_MAX_IDENT, generate_st, mirror_var_name
from .xsy_parser import fetch_export_xml, load_project_variables

Progress = Callable[[str], None]


@dataclass
class ProjectData:
    types: dict[str, DdtType] = field(default_factory=dict)
    tags: list[Tag] = field(default_factory=list)
    leaves: list[FlatLeaf] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # brownfield safety: existing address usage + our generated sections
    reserved: ReservedUsage = field(default_factory=ReservedUsage)
    generated_sections: list[GeneratedSection] = field(default_factory=list)


@dataclass
class ApplyReport:
    ok: bool = False
    created_vars: list[str] = field(default_factory=list)
    skipped_vars: list[str] = field(default_factory=list)
    build_state: str = ""
    build_output: str = ""
    error: str = ""
    saved: bool = False
    sidecar_path: str = ""
    csv_path: str = ""
    warnings: list[str] = field(default_factory=list)


def scan_project(bridge) -> ProjectData:
    """Fetch + flatten everything from the currently open CE project,
    including existing %M/%MW usage (brownfield collision floor) and any
    previously generated mirror sections (sidecar recovery input)."""
    types, tags = load_project_variables(bridge)
    # our own mirror variables are not candidate tags (but stay in `tags`
    # for the recovery path, which resolves them by their comment marker)
    own = [t for t in tags if not t.comment.startswith(MIRROR_COMMENT_PREFIX)]
    leaves, warnings = flatten_tags(own, types)
    data = ProjectData(types=types, tags=tags, leaves=leaves,
                       warnings=warnings)
    try:
        program_xml = fetch_export_xml(bridge, "program")
    except Exception as exc:  # empty program / export refused
        program_xml = None
        data.warnings.append(
            f"program export failed ({exc}) - existing %M/%MW literals in "
            "logic code could not be scanned; located variables still raise "
            "the allocation floor")
    data.generated_sections = find_generated_sections(program_xml)
    data.reserved = scan_reserved(
        tags, program_xml, {s.name for s in data.generated_sections})
    return data


def type_summary(data: ProjectData) -> list[dict]:
    """One row per shareable type for the type-selection screen."""
    rows: dict[str, dict] = {}
    for leaf in data.leaves:
        if leaf.ddt_type:
            key, kind = leaf.ddt_type, "DDT"
        else:
            key, kind = leaf.type_name, "Elementary"
        row = rows.setdefault(key, {"type": key, "kind": kind, "tags": set()})
        row["tags"].add(leaf.instance)
    return [
        {"type": r["type"], "kind": r["kind"], "count": len(r["tags"])}
        for r in sorted(rows.values(), key=lambda r: (r["kind"], r["type"].lower()))
    ]


def select_leaves(data: ProjectData, state: SidecarState) -> list[FlatLeaf]:
    """Leaves of the chosen types, minus user-unchecked ones, with access set.

    Type-level member exclusions ("TYPE|rel.path") apply to every instance of
    the DDT type — including instances that did not exist when the exclusion
    was made.
    """
    chosen = set(state.selected_types)
    unchecked = set(state.deselected_leaves)
    excluded_members = set(state.deselected_type_members)
    picked = [
        leaf for leaf in data.leaves
        if (leaf.ddt_type or leaf.type_name) in chosen
        and leaf.full_path not in unchecked
        and not (leaf.ddt_type and leaf.access_key in excluded_members)
    ]
    apply_access(picked, state.access_overrides)
    return picked


def build_plan(
    data: ProjectData, state: SidecarState, project_name: str = "",
    timestamp: str | None = None, word_bools: bool = False,
) -> tuple[MirrorPlan, AllocState]:
    """Allocate addresses and generate ST + CSV. Pure — no COM calls.

    Works on a copy of the allocation state; the caller persists it (via
    apply_plan) only after the project builds and saves successfully.

    `word_bools`: BOOLs mirror into %MW words (scanner topologies) instead
    of %M coils.
    """
    selected = select_leaves(data, state)
    alloc = copy.deepcopy(state.alloc)
    alloc.base_bit = state.settings.base_bit
    alloc.base_word = state.settings.base_word
    assignments = allocate(alloc, selected,
                           bit_floor=data.reserved.bit_floor,
                           word_floor=data.reserved.word_floor,
                           word_bools=word_bools)

    plan = MirrorPlan(assignments=assignments, warnings=list(data.warnings))
    if data.reserved.max_bit >= 0 or data.reserved.max_word >= 0:
        plan.warnings.append(
            f"project already uses %M up to {data.reserved.max_bit} and %MW "
            f"up to {data.reserved.max_word} ({data.reserved.n_located} "
            f"located variables, {data.reserved.n_literals} code literals) - "
            "new mirrors are allocated above that")

    used_names: dict[str, str] = {}
    for a in assignments:
        if a.premapped or not a.leaf.needs_var:
            continue
        # Distinct paths can sanitize to the same identifier (e.g. 'P1.A_B'
        # and 'P1.A.B') — dedupe with a numeric suffix, deterministic because
        # assignments keep source-declaration order across runs.
        base_name = mirror_var_name(a.leaf.full_path, state.settings.var_prefix)
        name, i = base_name, 2
        while name in used_names and used_names[name] != a.leaf.full_path:
            suffix = f"_{i}"
            name = base_name[: CE_MAX_IDENT - len(suffix)].rstrip("_") + suffix
            i += 1
        if name != base_name:
            plan.warnings.append(
                f"mirror name collision: '{base_name}' for "
                f"{used_names[base_name]} and {a.leaf.full_path} - "
                f"renamed to '{name}'"
            )
        used_names[name] = a.leaf.full_path
        a.mirror_var = name
        plan.new_variables.append({
            "name": name,
            "type_name": a.leaf.type_name,
            "comment": f"HMI mirror of {a.leaf.full_path}",
            "address": a.address,
        })

    if timestamp is None:
        timestamp = _dt.datetime.now().isoformat(timespec="seconds")
    plan.st_source = generate_st(
        assignments, project=project_name, version=__version__, timestamp=timestamp,
    )
    plan.csv_text = generate_csv(assignments)
    return plan, alloc


def apply_plan(
    bridge,
    plan: MirrorPlan,
    new_alloc: AllocState,
    state: SidecarState,
    project_path: str,
    progress: Progress = lambda msg: None,
) -> ApplyReport:
    """Create mirror variables, write the ST section, build, save, persist."""
    report = ApplyReport()
    settings = state.settings

    progress("Checking existing mirror variables...")
    existing: dict[str, dict] = {}
    listing = bridge.list_variables(settings.var_prefix or None, 100_000)
    for entry in listing.get("variables", []):
        existing[entry["name"]] = entry

    progress(f"Creating {len(plan.new_variables)} mirror variables...")
    for spec in plan.new_variables:
        found = existing.get(spec["name"])
        if found:
            same_addr = found.get("address", "") == spec["address"]
            same_type = found.get("type", "") == spec["type_name"]
            if same_addr and same_type:
                report.skipped_vars.append(spec["name"])
                continue
            report.error = (
                f"Variable '{spec['name']}' already exists with "
                f"type/address ({found.get('type')}, {found.get('address')}) != "
                f"planned ({spec['type_name']}, {spec['address']}). "
                "Project and sidecar have drifted - resolve manually."
            )
            return report
        bridge.create_variable(
            spec["name"], spec["type_name"], spec["comment"], spec["address"], None,
        )
        report.created_vars.append(spec["name"])

    progress(f"Writing ST section '{settings.section_name}'...")
    bridge.write_st_logic(settings.task_name, settings.section_name,
                          plan.st_source, None)

    progress("Building project...")
    build = bridge.build_project(True)
    report.build_state = build.get("build_state", "")
    report.build_output = build.get("output", "")
    if report.build_state != "built_ok":
        report.error = build.get("error") or (
            f"Build did not reach built_ok (state: {report.build_state}). "
            "See build output."
        )
        return report

    progress("Saving project...")
    bridge.save_project(None)
    report.saved = True

    state.alloc = new_alloc
    report.sidecar_path = save_sidecar(project_path, state)

    # The project is already built+saved and the sidecar persisted; a locked
    # CSV (open in Excel) must not turn that success into an apparent failure.
    csv_path = os.path.splitext(project_path)[0] + ".address_map.csv"
    try:
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as fh:
            fh.write(plan.csv_text)
        report.csv_path = csv_path
    except OSError as exc:
        report.warnings.append(
            f"address map CSV not written ({exc}) - close it if it is open "
            "in Excel; the CSV preview tab has the same content")

    report.ok = True
    progress("Done.")
    return report
