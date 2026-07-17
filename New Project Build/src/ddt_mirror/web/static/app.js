/* DDT Mirror web UI — vanilla SPA over the local API. */
"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  rows: [],                // variable rows from /api/variables
  selectedTypes: new Set(),
  overrides: {},           // access override keys -> "read"|"read_write"
  filter: { text: "", access: "", state: "", group: "type" },
  collapsed: new Set(),    // group keys folded shut in the tree
  libraryTypes: new Set(), // DDT types with saved global defaults
  plcPreview: null,
  plcTab: "st",
  rtuAssigned: false,
  activityCursor: 0,
  saveTimer: null,
};

/* ------------------------------------------------------------- helpers */
async function api(path, body, method) {
  const opts = { method: method || (body ? "POST" : "GET") };
  if (body) {
    opts.headers = { "Content-Type": "application/json" };
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(path, opts);
  if (!resp.ok) {
    let msg = resp.statusText;
    try { msg = (await resp.json()).detail || msg; } catch (e) { /* - */ }
    throw new Error(msg);
  }
  return resp.json();
}

function toast(text, kind) {
  const el = document.createElement("div");
  el.className = "toast " + (kind || "");
  el.textContent = text;
  $("toasts").appendChild(el);
  setTimeout(() => el.remove(), 5200);
}

/* ------------------------------------------------------ file picker */
function filePicker(title, exts, dirsOnly, startPath) {
  return new Promise((resolve) => {
    let cwd = startPath || localStorage.getItem("lastDir") || "";
    let selected = null;
    const back = $("picker"), list = $("picker-list");
    $("picker-title").textContent = title;
    $("picker-hint").textContent = dirsOnly
      ? "Open a folder, then Select chooses it."
      : `Showing ${exts} files`;

    async function load(path) {
      try {
        const d = await api(`/api/browse?path=${encodeURIComponent(path)}` +
                            `&ext=${dirsOnly ? "" : exts}`);
        cwd = d.cwd;
        selected = null;
        $("picker-ok").disabled = !dirsOnly || !cwd;
        $("picker-path").textContent = cwd || "This PC";
        let html = "";
        if (d.parent !== null && cwd)
          html += `<div class="picker-item" data-dir="${d.parent}">⬆️ ..</div>`;
        for (const dir of d.dirs)
          html += `<div class="picker-item" data-dir="${
            cwd ? cwd.replace(/\\$/, "") + "\\" + dir : dir}">📁 ${dir}</div>`;
        for (const f of d.files)
          html += `<div class="picker-item" data-file="${
            cwd.replace(/\\$/, "") + "\\" + f.name}">📄 ${f.name}
            <span class="meta">${f.mtime} · ${(f.size/1024).toFixed(0)} KB</span></div>`;
        list.innerHTML = html ||
          `<div class="picker-item muted">Empty folder</div>`;
      } catch (e) { toast(e.message, "error"); }
    }

    list.onclick = (ev) => {
      const el = ev.target.closest(".picker-item");
      if (!el) return;
      if (el.dataset.dir !== undefined) { load(el.dataset.dir); return; }
      list.querySelectorAll(".sel").forEach((x) => x.classList.remove("sel"));
      el.classList.add("sel");
      selected = el.dataset.file;
      $("picker-ok").disabled = false;
    };
    list.ondblclick = (ev) => {
      const el = ev.target.closest(".picker-item");
      if (el && el.dataset.file) done(true);
    };
    const done = (ok) => {
      back.classList.add("hidden");
      list.onclick = list.ondblclick = null;
      $("picker-ok").onclick = $("picker-cancel").onclick = null;
      if (ok && cwd) localStorage.setItem("lastDir", cwd);
      resolve(ok ? (dirsOnly ? cwd : selected) : null);
    };
    $("picker-ok").onclick = () => done(true);
    $("picker-cancel").onclick = () => done(false);
    back.classList.remove("hidden");
    load(cwd);
  });
}

function confirmModal(title, body, withInput, inputValue) {
  return new Promise((resolve) => {
    $("modal-title").textContent = title;
    $("modal-body").textContent = body;
    const input = $("modal-input");
    input.classList.toggle("hidden", !withInput);
    input.value = inputValue || "";
    $("modal").classList.remove("hidden");
    const done = (ok) => {
      $("modal").classList.add("hidden");
      $("modal-ok").onclick = $("modal-cancel").onclick = null;
      resolve(ok ? (withInput ? input.value : true) : null);
    };
    $("modal-ok").onclick = () => done(true);
    $("modal-cancel").onclick = () => done(false);
  });
}

function busy(btn, on, label) {
  if (on) {
    btn.dataset.label = btn.textContent;
    btn.innerHTML = `<span class="spin"></span>${label || btn.textContent}`;
    btn.disabled = true;
  } else {
    btn.textContent = btn.dataset.label;
    btn.disabled = false;
  }
}

/* ----------------------------------------------------------- selection */
function effectiveAccess(row) {
  return state.overrides["!" + row.path] || state.overrides[row.type_key]
    || row.access;
}

function visibleRows() {
  const f = state.filter;
  const needle = f.text.toLowerCase();
  return state.rows.filter((r) => {
    if (!state.selectedTypes.has(r.group)) return false;
    if (needle) {
      const hay = (r.path + " " + r.type + " " + r.comment).toLowerCase();
      if (!hay.includes(needle)) return false;
    }
    if (f.access && effectiveAccess(r) !== f.access) return false;
    if (f.state !== "" && String(+r.checked) !== f.state) return false;
    return true;
  });
}

function scheduleSave() {
  $("savestate").textContent = "saving…";
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(async () => {
    try {
      const unchecked = state.rows.filter((r) => !r.checked)
        .map((r) => r.path);
      await api("/api/selection", {
        selected_types: [...state.selectedTypes],
        unchecked,
        access_overrides: state.overrides,
      });
      $("savestate").textContent = "saved ✓";
      setTimeout(() => { $("savestate").textContent = ""; }, 1800);
      state.plcPreview = null;   // previews are stale now
      state.rtuAssigned = false;
      $("plc-generate").disabled = true;
      $("vijeo-export").disabled = true;
      $("rtu-generate").disabled = true;
    } catch (e) { toast("Save failed: " + e.message, "error"); }
  }, 500);
}

/* --------------------------------------------------------------- table */
function accessPill(row) {
  const acc = effectiveAccess(row);
  const label = acc === "read_write" ? "R/W" : "Read";
  return `<button class="access-pill ${acc}" data-path="${row.path}"
          title="Click to toggle Read / Read-Write">${label}</button>`;
}

function groupKey(r) {
  return state.filter.group === "type"
    ? r.group : (r.member ? r.instance : "— standalone —");
}

function renderTable() {
  const rows = visibleRows();
  const byType = state.filter.group === "type";
  const body = $("varbody");
  const groups = new Map();
  for (const r of rows) {
    const key = groupKey(r);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(r);
  }
  const parts = [];
  for (const [name, members] of groups) {
    const allOn = members.every((r) => r.checked);
    const someOn = members.some((r) => r.checked);
    const closed = state.collapsed.has(name);
    const star = byType && state.libraryTypes.has(name)
      ? ` <span class="star" title="Has saved global defaults">★</span>` : "";
    const insts = byType
      ? new Set(members.map((r) => r.instance)).size + " tags" : "";
    parts.push(`<tr class="group-row" data-gkey="${name}"><td>
      <input type="checkbox" data-group="${name}"
        ${allOn ? "checked" : ""} ${!allOn && someOn ? "data-mixed=1" : ""}>
      </td><td colspan="5"><span class="chev ${closed ? "closed" : ""}"
        >▶</span>${name}${star} <span class="count">${insts} ${
        closed ? "· " + members.length + " hidden" : ""}</span></td></tr>`);
    if (closed) continue;
    for (const r of members) {
      parts.push(`<tr class="${r.checked ? "" : "excluded"}" data-path="${r.path}">
        <td><input type="checkbox" data-path="${r.path}"
             ${r.checked ? "checked" : ""}></td>
        <td class="tag-name">${r.instance}</td>
        <td>${r.member || "<span class='muted'>—</span>"}</td>
        <td><span class="type-badge">${r.type}</span></td>
        <td>${accessPill(r)}</td>
        <td class="muted">${r.comment || ""}</td></tr>`);
    }
  }
  body.innerHTML = parts.join("") || `<tr><td colspan="6" class="muted"
     style="padding:30px;text-align:center">No variables match.</td></tr>`;
  body.querySelectorAll("input[data-mixed]").forEach(
    (cb) => { cb.indeterminate = true; });
  updateCounter(rows.length);
}

function updateCounter(shown) {
  $("counter").textContent =
    `${shown ?? visibleRows().length} of ${state.rows.length} variables · ` +
    `${state.rows.filter((r) => r.checked).length} included`;
}

function refreshGroupHeader(key) {
  const cb = $("varbody").querySelector(
    `input[data-group="${CSS.escape(key)}"]`);
  if (!cb) return;
  const members = state.rows.filter(
    (r) => state.selectedTypes.has(r.group) && groupKey(r) === key);
  const allOn = members.every((r) => r.checked);
  cb.checked = allOn;
  cb.indeterminate = !allOn && members.some((r) => r.checked);
}

/* Targeted update: flip N rows in place; full re-render only for big sets
   or when a state filter would change which rows are visible. */
function setChecked(paths, on) {
  const set = new Set(paths);
  for (const r of state.rows) if (set.has(r.path)) r.checked = on;
  if (paths.length > 200 || state.filter.state !== "") {
    renderTable();
  } else {
    const body = $("varbody");
    const touched = new Set();
    for (const p of paths) {
      const tr = body.querySelector(`tr[data-path="${CSS.escape(p)}"]`);
      if (!tr) continue;
      tr.classList.toggle("excluded", !on);
      const cb = tr.querySelector("input[type=checkbox]");
      if (cb) cb.checked = on;
      const row = state.rows.find((r) => r.path === p);
      if (row) touched.add(groupKey(row));
    }
    touched.forEach(refreshGroupHeader);
    updateCounter();
  }
  scheduleSave();
}

function updatePills(paths) {
  const body = $("varbody");
  for (const p of paths) {
    const row = state.rows.find((r) => r.path === p);
    const pill = body.querySelector(
      `.access-pill[data-path="${CSS.escape(p)}"]`);
    if (!row || !pill) continue;
    const acc = effectiveAccess(row);
    pill.className = "access-pill " + acc;
    pill.textContent = acc === "read_write" ? "R/W" : "Read";
  }
}

function toggleAccess(path) {
  const row = state.rows.find((r) => r.path === path);
  if (!row) return;
  const next = effectiveAccess(row) === "read" ? "read_write" : "read";
  const typeScope = state.filter.group === "type" && row.ddt_type;
  let affected = [path];
  if (typeScope) {
    state.overrides[row.type_key] = next;
    affected = [];
    for (const r of state.rows)
      if (r.type_key === row.type_key) {
        delete state.overrides["!" + r.path];
        affected.push(r.path);
      }
  } else {
    state.overrides["!" + path] = next;
  }
  if (state.filter.access) renderTable();   // filter may hide/show rows
  else updatePills(affected);
  scheduleSave();
}

/* ------------------------------------------------------------ variables */
async function loadVariables() {
  const v = await api("/api/variables");
  state.rows = v.rows;
  state.overrides = v.access_overrides;
  state.selectedTypes = new Set(
    v.selected_types.length ? v.selected_types
      : [...new Set(v.rows.map((r) => r.group))]);
  try {
    const lib = await api("/api/library");
    state.libraryTypes = new Set(lib.types);
  } catch (e) { /* non-fatal */ }
  renderTable();
}

/* -------------------------------------------------------------- project */
async function refreshStatus() {
  const s = await api("/api/status");
  $("ver").textContent = "v" + s.version;
  if (s.project) {
    $("project-chip").textContent = `${s.project} — ${s.leaves} tags`;
    $("project-chip").classList.add("loaded");
  }
  return s;
}

async function openProject() {
  const last = localStorage.getItem("lastProject");
  const path = await filePicker(
    "Open Control Expert project", ".stu,.sta", false,
    last ? last.substring(0, last.lastIndexOf("\\")) : "");
  if (!path) return;
  const btn = $("btn-open");
  busy(btn, true, "Opening…");
  try {
    const r = await api("/api/open", { path });
    localStorage.setItem("lastProject", path);
    await refreshStatus();
    await loadVariables();
    await loadOverview();
    toast("Project opened.", "ok");
    if (r.recovered)
      toast("No sidecar found — the address map was recovered from the " +
            "project itself. Review the selection before generating.", "warn");
  } catch (e) { toast("Open failed: " + e.message, "error"); }
  busy(btn, false);
}

async function loadOverview() {
  const p = await api("/api/project");
  const floor = p.reserved.max_bit < 0 && p.reserved.max_word < 0
    ? "none found"
    : `%M ≤ ${p.reserved.max_bit} · %MW ≤ ${p.reserved.max_word}`;
  $("ov-cards").innerHTML = `
    <div class="card"><h4>Mirrorable tags</h4><div class="big">${p.leaves}</div></div>
    <div class="card"><h4>DDT types</h4><div class="big">${p.ddt_types}</div></div>
    <div class="card"><h4>Existing address usage</h4><div>${floor}</div>
      <div class="muted">${p.reserved.located} located · ${p.reserved.literals} literals</div></div>
    <div class="card"><h4>Allocated so far</h4>
      <div>${p.alloc_count} PLC mirrors</div><div>${p.rtu_count} RTU objects</div></div>
    <div class="card"><h4>Generated sections</h4>
      <div>${p.generated_sections.join(", ") || "none"}</div></div>`;
  $("ov-warnings").textContent = p.warnings.join("\n") || "(none)";
}

/* ------------------------------------------------------------------ PLC */
async function plcPreview() {
  const btn = $("plc-preview");
  busy(btn, true, "Assigning…");
  try {
    state.plcPreview = await api("/api/plc/preview", {});
    renderPlcPane();
    $("plc-generate").disabled = false;
    $("vijeo-export").disabled = false;
    $("plc-warn-count").textContent = state.plcPreview.warnings.length || "";
    toast("Addresses assigned — review, then generate.", "ok");
  } catch (e) { toast(e.message, "error"); }
  busy(btn, false);
}

function renderPlcPane() {
  const p = state.plcPreview;
  if (!p) return;
  const panes = {
    st: p.st,
    vars: p.new_variables.map(
      (v) => `${v.name}  :  ${v.type_name}  AT  ${v.address}`).join("\n")
      || "(no new variables needed)",
    csv: p.csv,
    warn: p.warnings.join("\n") || "(none)",
  };
  $("plc-pane").textContent = panes[state.plcTab];
}

async function plcGenerate() {
  const p = state.plcPreview;
  const ok = await confirmModal("Generate into project",
    `This creates ${p.new_variables.length} mirror variables, writes the ` +
    "mirror ST section, rebuilds and saves the project.");
  if (!ok) return;
  const btn = $("plc-generate");
  busy(btn, true, "Generating…");
  try {
    const r = await api("/api/plc/generate", {});
    if (r.ok) {
      $("plc-result").innerHTML =
        `<span class="ok">Done.</span> Created ${r.created.length} variables ` +
        `(skipped ${r.skipped.length} existing), build ${r.build_state}, saved.` +
        `<br>Address map: ${r.csv_path || "(not written)"}` +
        r.warnings.map((w) => `<br><span class="warn">⚠ ${w}</span>`).join("");
      toast("Generation complete.", "ok");
    } else {
      $("plc-result").innerHTML = `<span class="err">Failed:</span> ${r.error}`;
      if (r.build_output) { $("plc-pane").textContent = r.build_output; }
      toast("Generate failed — see result.", "error");
    }
  } catch (e) { toast(e.message, "error"); }
  busy(btn, false);
}

async function vijeoExport() {
  const group = await confirmModal("Export Vijeo files",
    "Scan group name of your Modbus TCP equipment in Vijeo (IEC61131 " +
    "syntax must be enabled on it). Files are written next to the project.",
    true, "ModbusEquipment01");
  if (!group) return;
  try {
    const r = await api("/api/vijeo/export", { scan_group: group });
    $("plc-result").innerHTML =
      `<span class="ok">Vijeo files written.</span><ul>` +
      `<li>${r.udt_path} <span class="muted">(import FIRST — User Data Types)</span></li>` +
      `<li>${r.csv_path} <span class="muted">(then Variables → Import → CSV)</span></li></ul>` +
      r.warnings.slice(0, 8).map((w) => `<span class="warn">⚠ ${w}</span><br>`).join("");
    toast("Vijeo export written.", "ok");
  } catch (e) { toast(e.message, "error"); }
}

/* ------------------------------------------------------------------ RTU */
function rtuReq() {
  return {
    src_xls: $("rtu-src").value.trim(),
    mode: $("rtu-mode").value,
    device: $("rtu-device").value.trim(),
    hmi_index_base: +$("rtu-index").value,
    out_dir: $("rtu-out").value.trim(),
  };
}

async function rtuAssign() {
  const btn = $("rtu-assign");
  busy(btn, true, "Assigning…");
  try {
    const r = await api("/api/rtu/assign", rtuReq());
    const head = r.mode === "scanner"
      ? `Device '${r.device}' — ${r.created} new objects, ${r.blocks} scan ` +
        `blocks, ${r.bindings} bindings (+${r.plc_variables} PLC mirror vars)`
      : `Device type ${r.device_type || "unknown"} — ${r.created} new ` +
        `objects, ${r.existing} already in the workbook`;
    $("rtu-pane").textContent =
      head + "\n" + "=".repeat(70) + "\n" + r.map_csv +
      (r.warnings.length ? "\n--- warnings ---\n" + r.warnings.join("\n") : "");
    $("rtu-generate").disabled = false;
    state.rtuAssigned = true;
    toast("Addresses assigned — review the map, then generate.", "ok");
  } catch (e) { toast(e.message, "error"); }
  busy(btn, false);
}

async function rtuGenerate() {
  const req = rtuReq();
  const ok = await confirmModal("Generate transfer bundle",
    req.mode === "scanner"
      ? "This applies the PLC mirror into the Control Expert project " +
        "(build + save), then writes the enriched RemoteConnect .xls."
      : "This writes the RemoteConnect bundle (.xls, mirror .st, maps, " +
        ".xsy, section files).");
  if (!ok) return;
  const btn = $("rtu-generate");
  busy(btn, true, "Generating…");
  try {
    const r = await api("/api/rtu/generate", req);
    if (r.ok) {
      $("rtu-result").innerHTML =
        `<span class="ok">Bundle complete.</span><ul>` +
        r.files.map((f) => `<li>${f}</li>`).join("") + "</ul>" +
        r.warnings.slice(0, 10).map(
          (w) => `<span class="warn">⚠ ${w}</span><br>`).join("");
      toast("Transfer bundle written.", "ok");
    } else {
      $("rtu-result").innerHTML =
        `<span class="err">Failed at the PLC step:</span> ${r.error} — ` +
        "the workbook was NOT modified.";
      toast("Generate failed.", "error");
    }
  } catch (e) { toast(e.message, "error"); }
  busy(btn, false);
}

/* --------------------------------------------------------------- wiring */
function segWire(id, key, after) {
  $(id).addEventListener("click", (ev) => {
    const b = ev.target.closest("button");
    if (!b) return;
    $(id).querySelectorAll("button").forEach((x) => x.classList.remove("on"));
    b.classList.add("on");
    state.filter[key] = b.dataset.v;
    (after || renderTable)();
  });
}

function wire() {
  document.querySelectorAll(".rail-item").forEach((b) => {
    b.onclick = () => {
      document.querySelectorAll(".rail-item").forEach(
        (x) => x.classList.remove("active"));
      b.classList.add("active");
      document.querySelectorAll(".page").forEach(
        (p) => p.classList.add("hidden"));
      $("page-" + b.dataset.page).classList.remove("hidden");
    };
  });
  $("btn-open").onclick = openProject;
  let searchTimer = null;
  $("search").addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.filter.text = e.target.value;
      renderTable();
    }, 160);
  });
  segWire("seg-access", "access");
  segWire("seg-state", "state");
  segWire("seg-group", "group");

  $("varbody").addEventListener("click", (ev) => {
    const pill = ev.target.closest(".access-pill");
    if (pill) { toggleAccess(pill.dataset.path); return; }
    const cb = ev.target.closest("input[type=checkbox]");
    if (!cb) {
      const grow = ev.target.closest("tr.group-row");
      if (grow) {                       // toggle collapse on row click
        const key = grow.dataset.gkey;
        if (state.collapsed.has(key)) state.collapsed.delete(key);
        else state.collapsed.add(key);
        renderTable();
      }
      return;
    }
    if (cb.dataset.path) setChecked([cb.dataset.path], cb.checked);
    else if (cb.dataset.group !== undefined) {
      const byType = state.filter.group === "type";
      const members = visibleRows().filter((r) =>
        (byType ? r.group : (r.member ? r.instance : "— standalone —"))
          === cb.dataset.group);
      setChecked(members.map((r) => r.path), cb.checked);
    }
  });
  $("head-check").onchange = (e) =>
    setChecked(visibleRows().map((r) => r.path), e.target.checked);
  $("expand-all").onclick = () => { state.collapsed.clear(); renderTable(); };
  $("collapse-all").onclick = () => {
    const byType = state.filter.group === "type";
    state.collapsed = new Set(visibleRows().map((r) =>
      byType ? r.group : (r.member ? r.instance : "— standalone —")));
    renderTable();
  };
  $("bulk-include").onclick = () =>
    setChecked(visibleRows().map((r) => r.path), true);
  $("bulk-exclude").onclick = () =>
    setChecked(visibleRows().map((r) => r.path), false);
  const bulkAccess = (value) => {
    for (const r of visibleRows()) {
      if (state.filter.group === "type" && r.ddt_type) {
        state.overrides[r.type_key] = value;
        delete state.overrides["!" + r.path];
      } else state.overrides["!" + r.path] = value;
    }
    renderTable();
    scheduleSave();
  };
  $("bulk-read").onclick = () => bulkAccess("read");
  $("bulk-rw").onclick = () => bulkAccess("read_write");
  $("lib-save").onclick = async () => {
    const ddts = [...new Set(state.rows.filter(
      (r) => r.ddt_type && state.selectedTypes.has(r.group))
      .map((r) => r.ddt_type))];
    if (!ddts.length) { toast("No DDT types selected.", "warn"); return; }
    const ok = await confirmModal("Save DDT defaults",
      `Save the current member selection and Read/R-W choices of ` +
      `${ddts.join(", ")} as GLOBAL defaults? Every project you open ` +
      "later that uses these DDT type names is configured automatically.");
    if (!ok) return;
    try {
      const r = await api("/api/library/save", { types: ddts });
      r.saved.forEach((t) => state.libraryTypes.add(t));
      renderTable();
      toast(`Defaults saved for ${r.saved.join(", ")} — applied ` +
            "automatically on every future project.", "ok");
    } catch (e) { toast(e.message, "error"); }
  };

  $("plc-preview").onclick = plcPreview;
  $("plc-generate").onclick = plcGenerate;
  $("vijeo-export").onclick = vijeoExport;
  $("plc-tabs").addEventListener("click", (ev) => {
    const b = ev.target.closest("button");
    if (!b) return;
    $("plc-tabs").querySelectorAll("button").forEach(
      (x) => x.classList.remove("on"));
    b.classList.add("on");
    state.plcTab = b.dataset.t;
    renderPlcPane();
  });

  $("rtu-src-browse").onclick = async () => {
    const p = await filePicker("RemoteConnect export", ".xls", false);
    if (p) { $("rtu-src").value = p; $("rtu-generate").disabled = true; }
  };
  $("rtu-out-browse").onclick = async () => {
    const p = await filePicker("Output folder", "", true);
    if (p) $("rtu-out").value = p;
  };
  $("rtu-mode").onchange = () => {
    $("rtu-device-wrap").classList.toggle(
      "hidden", $("rtu-mode").value !== "scanner");
    $("rtu-generate").disabled = true;
  };
  $("rtu-assign").onclick = rtuAssign;
  $("rtu-generate").onclick = rtuGenerate;
}

async function pollActivity() {
  try {
    const a = await api(`/api/activity?after=${state.activityCursor}`);
    if (a.items.length) {
      const box = $("activity");
      for (const it of a.items) {
        const div = document.createElement("div");
        div.className = "line " + it.kind;
        div.textContent = `[${it.time}] ${it.text}`;
        box.appendChild(div);
      }
      box.scrollTop = box.scrollHeight;
      state.activityCursor = a.next;
    }
  } catch (e) { /* server restarting */ }
  setTimeout(pollActivity, 1500);
}

(async function init() {
  wire();
  const s = await refreshStatus();
  if (s.project) { await loadVariables(); await loadOverview(); }
  pollActivity();
})();
