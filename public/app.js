"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const pctClass = (p) => (p >= 90 ? "g" : p >= 75 ? "a" : "r");
const barColor = (p) => (p >= 90 ? "#16a34a" : p >= 75 ? "#f59e0b" : "#ef4444");

// ---- Auth ----
function getPw() { return sessionStorage.getItem("app_pw") || ""; }
function authHeaders() { const pw = getPw(); return pw ? { "X-App-Password": pw } : {}; }

async function initAuth() {
  try {
    const cfg = await (await fetch("/api/config")).json();
    if (cfg.auth_required && !getPw()) showLogin();
    else loadMain();
  } catch (e) { loadMain(); }
}
function showLogin() { $("login").hidden = false; }
$("login-btn").addEventListener("click", async () => {
  const pw = $("login-pw").value;
  const res = await fetch("/api/records", { headers: { "X-App-Password": pw } });
  if (res.ok) {
    sessionStorage.setItem("app_pw", pw);
    $("login").hidden = true;
    loadMain();
  } else { $("login-err").textContent = "Incorrect password."; }
});

// ---- Tabs ----
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $(btn.dataset.tab).classList.add("active");
    if (btn.dataset.tab === "main") loadMain();
    if (btn.dataset.tab === "archive") loadArchive();
  });
});

async function postForm(url, fd, statusEl, btn) {
  statusEl.className = "status"; statusEl.textContent = "Working…"; btn.disabled = true;
  try {
    const res = await fetch(url, { method: "POST", body: fd, headers: authHeaders() });
    const data = await res.json();
    if (res.status === 401) { showLogin(); throw new Error("Password required."); }
    if (!res.ok) throw new Error(data.detail || res.statusText);
    statusEl.textContent = "Done."; return data;
  } catch (e) {
    statusEl.className = "status err"; statusEl.textContent = "Error: " + e.message; return null;
  } finally { btn.disabled = false; }
}

function download(name, text, type) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: type || "text/csv" }));
  a.download = name; a.click();
}

// ================ MAIN TAB — current active roster ================
const ROSTER_SHIFTS = ["Night", "Afternoon", "Morning"];
const ROSTER_DAYWEEKS = ["Sun-Thurs", "Tues-Sat", "Mon-Fri"];
let _mainAlloc = [];
let _mainMonth = null;

async function loadMain() {
  const statusEl = $("main-status");
  statusEl.className = "status"; statusEl.textContent = "Loading roster…";
  try {
    const res = await fetch("/api/roster/latest", { headers: authHeaders() });
    if (res.status === 401) { showLogin(); return; }
    const d = await res.json();
    statusEl.textContent = "";
    if (!d.month) {
      // API returned empty (Vercel without Blob, or genuinely no roster yet).
      // Fall back to the locally cached copy from the last recommend run.
      try {
        const cached = localStorage.getItem("roster_cache");
        if (cached) {
          const c = JSON.parse(cached);
          _mainAlloc = c.allocation;
          _mainMonth = c.month;
          $("main-month").textContent = `Current Roster — ${c.month}`;
          $("main-saved-at").textContent = `Cached locally — ${c.saved_at.slice(0, 16).replace("T", " ")} UTC`;
          $("main-header").hidden = false;
          $("main-save").hidden = true;
          renderMainRoster(c.allocation);
          return;
        }
      } catch (_) {}
      $("main-header").hidden = true;
      $("main-results").innerHTML = `<div class="card"><p class="note" style="font-size:15px;padding:8px 0">
        No roster saved yet.<br>Go to <b>Roster scheduling</b> to generate one — it will appear here automatically.</p></div>`;
      return;
    }
    _mainAlloc = d.allocation;
    _mainMonth = d.month;
    $("main-month").textContent = `Current Roster — ${d.month}`;
    $("main-saved-at").textContent = d.saved_at
      ? `Last updated: ${d.saved_at.slice(0, 16).replace("T", " ")} UTC`
      : "";
    $("main-header").hidden = false;
    $("main-save").hidden = true;
    renderMainRoster(d.allocation);
  } catch (e) {
    statusEl.className = "status err";
    statusEl.textContent = "Could not load roster: " + e.message;
  }
}

function renderMainRoster(alloc) {
  const rotating = alloc.filter((r) => !r.fixed);
  const fixed = alloc.filter((r) => r.fixed);

  let html = `<div class="card"><table class="main-roster-table"><thead>
    <tr><th>Agent</th><th>Shift</th><th>Day week</th><th></th></tr></thead><tbody>`;

  ROSTER_SHIFTS.forEach((shift) => {
    const rows = rotating.filter((r) => r.shift === shift);
    if (!rows.length) return;
    html += `<tr class="shift-header-row"><td colspan="4">${shift}</td></tr>`;
    rows.forEach((r) => {
      const shiftOpts = ROSTER_SHIFTS.map((s) =>
        `<option value="${s}"${s === r.shift ? " selected" : ""}>${s}</option>`).join("");
      const daysOpts = ["Sun-Thurs", "Tues-Sat"].map((d) =>
        `<option value="${d}"${d === r.days ? " selected" : ""}>${d}</option>`).join("");
      html += `<tr data-agent="${esc(r.agent)}">
        <td>${esc(r.agent)}</td>
        <td><select class="main-sel" data-field="shift">${shiftOpts}</select></td>
        <td><select class="main-sel" data-field="days">${daysOpts}</select></td>
        <td></td></tr>`;
    });
  });

  if (fixed.length) {
    html += `<tr class="shift-header-row"><td colspan="4">Fixed (Mon-Fri)</td></tr>`;
    fixed.forEach((r) => {
      html += `<tr><td>${esc(r.agent)}</td>
        <td class="muted">${esc(r.shift)}</td>
        <td class="muted">${esc(r.days)}</td>
        <td><span class="tag">fixed</span></td></tr>`;
    });
  }

  html += `</tbody></table></div>`;
  $("main-results").innerHTML = html;

  // wire up change detection
  document.querySelectorAll(".main-sel").forEach((sel) => {
    sel.addEventListener("change", () => {
      sel.closest("tr").classList.add("row-changed");
      $("main-save").hidden = false;
    });
  });
}

$("main-save").addEventListener("click", async () => {
  const rows = [];
  document.querySelectorAll("#main-results tbody tr[data-agent]").forEach((tr) => {
    rows.push({
      agent: tr.dataset.agent,
      shift: tr.querySelector("[data-field=shift]").value,
      days: tr.querySelector("[data-field=days]").value,
      fixed: false,
    });
  });
  // append fixed agents unchanged
  _mainAlloc.filter((r) => r.fixed).forEach((r) => rows.push(r));

  const fd = new FormData();
  fd.append("month", _mainMonth);
  fd.append("allocation", JSON.stringify(rows));
  const data = await postForm("/api/roster/save", fd, $("main-status"), $("main-save"));
  if (data) {
    _mainAlloc = rows;
    $("main-save").hidden = true;
    document.querySelectorAll(".row-changed").forEach((tr) => tr.classList.remove("row-changed"));
    const ts = new Date().toISOString();
    $("main-saved-at").textContent = `Last updated: ${ts.slice(0, 16).replace("T", " ")} UTC`;
    try {
      localStorage.setItem("roster_cache", JSON.stringify({
        month: _mainMonth, allocation: rows, saved_at: ts
      }));
    } catch (_) {}
  }
});

$("main-dl-csv").addEventListener("click", () => {
  if (!_mainAlloc.length) return;
  download(`roster_${_mainMonth || "current"}.csv`, toCSV(_mainAlloc));
});

// ================ ATTENDANCE ================
let _perAgent = [];
$("att-run").addEventListener("click", async () => {
  const fd = new FormData();
  const zf = $("att-zendesk-file").files[0];
  if (!zf) { $("att-status").className = "status err"; $("att-status").textContent = "Upload the Zendesk export CSV."; return; }
  fd.append("zendesk_file", zf);
  if ($("att-roster-url").value.trim()) fd.append("roster_url", $("att-roster-url").value.trim());
  if ($("att-roster-file").files[0]) fd.append("roster_file", $("att-roster-file").files[0]);
  fd.append("export_tz", $("att-tz").value);
  const data = await postForm("/api/attendance", fd, $("att-status"), $("att-run"));
  if (data) renderAttendance(data);
});

function renderAttendance(d) {
  const s = d.summary;
  const kpis = [["Attendance", s.attendance_pct + "%"], ["On time", s.on_time_pct + "%"],
    ["Avg coverage", s.avg_coverage_pct + "%"], ["Late", s.late], ["Absent", s.absent]];
  let html = `<p class="note">Period ${esc(d.range[0])} → ${esc(d.range[1])} · ${s.scheduled} scheduled shifts</p>`;
  html += `<div class="kpis">` + kpis.map(([l, v]) =>
    `<div class="kpi"><div class="label">${l}</div><div class="value">${v}</div></div>`).join("") + `</div>`;
  if (d.unmapped && d.unmapped.length)
    html += `<div class="warn">⚠️ No Zendesk match for: ${esc(d.unmapped.join(", "))}</div>`;

  _perAgent = d.per_agent.slice().sort((a, b) => b.attendance_pct - a.attendance_pct);
  html += `<h2>Attendance by agent
    <button class="secondary" id="dl-csv">⬇️ CSV</button>
    <button class="secondary" id="dl-xlsx">⬇️ Excel</button></h2>`;
  html += `<table class="att-table"><thead><tr><th>Agent</th><th>Attendance</th><th class="num">On/Late/Abs</th><th class="num">Coverage</th><th></th></tr></thead><tbody>`;
  _perAgent.forEach((a) => {
    html += `<tr>
      <td title="${esc(a.agent)}">${esc(a.agent)}</td>
      <td><div class="bar-cell"><span class="bar-wrap"><span class="bar" style="width:${Math.max(a.attendance_pct, 2)}%;background:${barColor(a.attendance_pct)}"></span></span><span class="pct ${pctClass(a.attendance_pct)}">${a.attendance_pct}%</span></div></td>
      <td class="num">${a.on_time}/${a.late}/${a.absent}</td>
      <td class="num">${a.coverage_pct}%</td>
      <td><span class="tag">${esc(a.note)}</span></td></tr>`;
  });
  html += `</tbody></table>`;

  html += `<h2>Weekly attendance</h2><div class="weeks">` + weeklyCalendar(d.grid) + `</div>
    <p class="legend"><span class="pct g">P</span> on time &nbsp;
      <span class="pct a">L</span> late &nbsp;
      <span class="pct r">A</span> absent &nbsp;
      <span class="pct" style="background:var(--blue-bg);color:#1e40af">E</span> leave &nbsp;
      <span style="color:var(--muted)">·</span> off</p>`;
  $("att-results").innerHTML = html;
  $("dl-csv").onclick = () => download("attendance.csv", toCSV(_perAgent));
  $("dl-xlsx").onclick = () => {
    const ws = XLSX.utils.json_to_sheet(_perAgent);
    const wb = XLSX.utils.book_new(); XLSX.utils.book_append_sheet(wb, ws, "Attendance");
    XLSX.writeFile(wb, "attendance.xlsx");
  };
}

function toCSV(rows) {
  if (!rows.length) return "";
  const cols = Object.keys(rows[0]);
  return cols.join(",") + "\n" + rows.map((r) => cols.map((c) =>
    `"${String(r[c]).replace(/"/g, '""')}"`).join(",")).join("\n");
}

const ABBR = { on_time: "P", late: "L", absent: "A", excused: "E", off: "·", "": "·" };
const WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
function mondayOf(d) { const x = new Date(d + "T00:00:00"); const wd = (x.getDay() + 6) % 7; x.setDate(x.getDate() - wd); return x; }
// Use local date parts — toISOString() returns UTC and shifts date by -1 day in UTC+ timezones
function ymd(dt) { return dt.getFullYear() + '-' + String(dt.getMonth() + 1).padStart(2, '0') + '-' + String(dt.getDate()).padStart(2, '0'); }

function weeklyCalendar(grid) {
  if (!grid || !grid.rows || !grid.dates || !grid.dates.length) return `<p class="note">No calendar data.</p>`;
  const stat = {};
  grid.rows.forEach((r) => { stat[r.agent] = {}; grid.dates.forEach((dt, i) => { stat[r.agent][dt] = r.cells[i]; }); });
  const agents = grid.rows.map((r) => r.agent);
  const weeks = {};
  grid.dates.forEach((dt) => { const k = ymd(mondayOf(dt)); (weeks[k] = weeks[k] || []).push(dt); });
  let out = "";
  Object.keys(weeks).sort().forEach((wk) => {
    const mon = new Date(wk + "T00:00:00");
    const cols = [...Array(7)].map((_, i) => { const dd = new Date(mon); dd.setDate(mon.getDate() + i); return ymd(dd); });
    out += `<div class="week card"><h3>Week of ${wk}</h3><div class="cal"><table><thead><tr><th>Agent</th>`;
    cols.forEach((c, i) => out += `<th>${WD[i]}<br><span style="font-weight:400;font-size:11px">${c.slice(5)}</span></th>`);
    out += `</tr></thead><tbody>`;
    agents.forEach((ag) => {
      out += `<tr><td>${esc(ag)}</td>`;
      cols.forEach((c) => {
        const s = (stat[ag] || {})[c];
        const cls = s ? s : "off";
        out += `<td class="cell s-${cls}">${ABBR[s] ?? "·"}</td>`;
      });
      out += `</tr>`;
    });
    out += `</tbody></table></div></div>`;
  });
  return out;
}

// ================ ROSTER SCHEDULING ================
function readTargets() {
  const t = {};
  ["Morning", "Afternoon", "Night"].forEach((s) => {
    t[s] = {}; ["Sun-Thurs", "Tues-Sat"].forEach((d) => {
      t[s][d] = parseInt($(`t-${s}-${d}`).value || "0", 10);
    });
  });
  return t;
}

$("ros-run").addEventListener("click", async () => {
  const fd = new FormData();
  if ($("ros-roster-url").value.trim()) fd.append("roster_url", $("ros-roster-url").value.trim());
  if ($("ros-roster-file").files[0]) fd.append("roster_file", $("ros-roster-file").files[0]);
  if (!$("ros-roster-url").value.trim() && !$("ros-roster-file").files[0]) {
    $("ros-status").className = "status err"; $("ros-status").textContent = "Provide a roster link or CSV."; return;
  }
  if ($("ros-hist-url").value.trim()) fd.append("history_url", $("ros-hist-url").value.trim());
  if ($("ros-hist-file").files[0]) fd.append("history_file", $("ros-hist-file").files[0]);
  if ($("ros-month").value.trim()) fd.append("month", $("ros-month").value.trim());
  fd.append("targets", JSON.stringify(readTargets()));
  const data = await postForm("/api/roster/recommend", fd, $("ros-status"), $("ros-run"));
  if (data) renderRoster(data);
});

function renderRoster(d) {
  const days = ["Sun-Thurs", "Tues-Sat"], shifts = ["Night", "Afternoon", "Morning"];
  let html = `<div class="warn" style="background:var(--green-bg);color:#166534">
    ✅ Roster saved for <b>${esc(d.month)}</b> — click <b>Main</b> to view and edit it.</div>`;
  html += `<p class="note">Fairness spread: shift avg ${d.avg_shift_spread} (max ${d.max_shift_spread}), day-week avg ${d.avg_day_spread} — lower is fairer.</p>`;
  if (d.unfilled_slots) html += `<div class="warn">⚠️ ${d.unfilled_slots} slot(s) unfilled (pool smaller than targets).</div>`;
  if (d.unassigned_agents && d.unassigned_agents.length) html += `<div class="warn">⚠️ Not placed: ${esc(d.unassigned_agents.join(", "))}</div>`;

  html += `<h2>Coverage grid</h2><table class="cov-grid"><thead><tr><th>Shift</th>` +
    days.map((dd) => `<th>${dd}</th>`).join("") + `</tr></thead><tbody>`;
  shifts.forEach((s) => {
    html += `<tr><td>${s}</td>` + days.map((dd) => `<td class="n">${(d.coverage[s] || {})[dd] || 0}</td>`).join("") + `</tr>`;
  });
  html += `</tbody></table>`;

  html += `<h2>Allocation</h2><div class="shift-cols">`;
  shifts.forEach((s) => {
    const rows = d.allocation.filter((r) => r.shift === s);
    html += `<div class="shift-col ${s}"><h3>${s} <span class="day">${rows.length}</span></h3>`;
    rows.forEach((r) => html += `<div class="row">${esc(r.agent)} <span class="day">· ${esc(r.days)}${r.fixed ? " · fixed" : ""}</span></div>`);
    html += `</div>`;
  });
  html += `</div>`;

  html += `<h2>History rows to append <button class="secondary" id="dl-hist">⬇️ history CSV</button>
    <button class="secondary" id="dl-alloc">⬇️ allocation CSV</button></h2>
    <pre class="csv">${esc(d.history_csv)}</pre>`;
  $("ros-results").innerHTML = html;
  $("dl-hist").onclick = () => download("shift_history_append.csv", d.history_csv);
  $("dl-alloc").onclick = () => download("roster_" + d.month + ".csv", d.allocation_csv);

  // Cache the roster locally so the Main tab can show it even when Vercel Blob
  // isn't configured (serverless containers have no shared filesystem).
  try {
    localStorage.setItem("roster_cache", JSON.stringify({
      month: d.month, allocation: d.allocation, saved_at: new Date().toISOString()
    }));
  } catch (_) {}
}

// ================ ROSTER HISTORY ================
let _histRecords = [];

$("hist-run").addEventListener("click", async () => {
  const fd = new FormData();
  const url = $("hist-url").value.trim();
  const file = $("hist-file").files[0];
  if (!url && !file) {
    $("hist-status").className = "status err"; $("hist-status").textContent = "Provide a history link or CSV."; return;
  }
  if (url) fd.append("roster_url", url);
  if (file) fd.append("history_file", file);
  const data = await postForm("/api/roster/history", fd, $("hist-status"), $("hist-run"));
  if (data) {
    _histRecords = data.records;
    renderHistoryControls(data.months);
  }
});

function renderHistoryControls(months) {
  if (!months.length) { $("hist-results").innerHTML = `<p class="note">No data found in the uploaded file.</p>`; return; }
  let sel = `<div class="hist-filter"><label>Filter by month <select id="hist-month">`;
  months.slice().reverse().forEach((m) => sel += `<option value="${esc(m)}">${esc(m)}</option>`);
  sel += `</select></label></div><div id="hist-alloc"></div>`;
  $("hist-results").innerHTML = sel;
  renderHistory(_histRecords, months[months.length - 1]);
  $("hist-month").addEventListener("change", () => renderHistory(_histRecords, $("hist-month").value));
}

function renderHistory(records, month) {
  const rows = records.filter((r) => r.month === month);
  const el = $("hist-alloc");
  if (!el) return;
  if (!rows.length) { el.innerHTML = `<p class="note">No entries for ${esc(month)}.</p>`; return; }
  const shifts = ["Night", "Afternoon", "Morning"];
  let html = `<p class="note"><b>${esc(month)}</b> — ${rows.length} agent assignment(s)</p><div class="shift-cols">`;
  shifts.forEach((s) => {
    const sr = rows.filter((r) => r.shift === s);
    if (!sr.length) return;
    html += `<div class="shift-col ${s}"><h3>${s} <span class="day">${sr.length}</span></h3>`;
    sr.forEach((r) => html += `<div class="row">${esc(r.agent)}<span class="day"> · ${esc(r.days || "")}</span></div>`);
    html += `</div>`;
  });
  const other = rows.filter((r) => !shifts.includes(r.shift));
  if (other.length) {
    html += `<div class="shift-col" style="background:linear-gradient(120deg,#374151,#6b7280)"><h3>Other <span class="day">${other.length}</span></h3>`;
    other.forEach((r) => html += `<div class="row">${esc(r.agent)}<span class="day"> · ${esc(r.shift)} · ${esc(r.days || "")}</span></div>`);
    html += `</div>`;
  }
  html += `</div>`;
  el.innerHTML = html;
}

// ================ ARCHIVE ================
$("arc-refresh").addEventListener("click", loadArchive);
async function loadArchive() {
  $("arc-status").textContent = "Loading…";
  try {
    const res = await fetch("/api/records", { headers: authHeaders() });
    if (res.status === 401) { showLogin(); throw new Error("Password required."); }
    const d = await res.json();
    $("arc-status").textContent = "";
    const recs = d.records || [];
    if (!recs.length) { $("arc-results").innerHTML = `<p class="note">No saved records yet.</p>`; return; }
    let html = `<table><thead><tr><th>Item</th><th>Uploaded</th><th class="num">Size</th><th></th></tr></thead><tbody>`;
    recs.forEach((r) => {
      const kb = r.size ? (r.size / 1024).toFixed(1) + " KB" : "";
      html += `<tr><td>${esc(r.key)}</td><td>${esc((r.uploaded_at || "").slice(0, 19).replace("T", " "))}</td>
        <td class="num">${kb}</td><td><a href="${esc(r.url)}" target="_blank">open</a></td></tr>`;
    });
    $("arc-results").innerHTML = html + `</tbody></table>`;
  } catch (e) { $("arc-status").className = "status err"; $("arc-status").textContent = e.message; }
}

initAuth();
