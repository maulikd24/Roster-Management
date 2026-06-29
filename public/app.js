"use strict";

// Tab switching
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(btn.dataset.tab).classList.add("active");
  });
});

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

function barColor(p) { return p >= 90 ? "#1d9e75" : p >= 75 ? "#ef9f27" : "#e24b4a"; }

async function postForm(url, fd, statusEl, btn) {
  statusEl.className = "status";
  statusEl.textContent = "Working…";
  btn.disabled = true;
  try {
    const res = await fetch(url, { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.statusText);
    statusEl.textContent = "Done.";
    return data;
  } catch (e) {
    statusEl.className = "status err";
    statusEl.textContent = "Error: " + e.message;
    return null;
  } finally {
    btn.disabled = false;
  }
}

// ---------------- Attendance ----------------
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
  const kpis = [
    ["Attendance", s.attendance_pct + "%"], ["On time", s.on_time_pct + "%"],
    ["Avg coverage", s.avg_coverage_pct + "%"], ["Late", s.late], ["Absent", s.absent],
  ];
  let html = `<p class="note">Period: ${esc(d.range[0])} → ${esc(d.range[1])} · ${s.scheduled} scheduled shifts</p>`;
  html += `<div class="kpis">` + kpis.map(([l, v]) =>
    `<div class="kpi"><div class="label">${l}</div><div class="value">${v}</div></div>`).join("") + `</div>`;

  if (d.unmapped && d.unmapped.length)
    html += `<p class="note">⚠️ No Zendesk match for: ${esc(d.unmapped.join(", "))}</p>`;

  html += `<h2>Attendance by agent</h2><table><thead><tr>
    <th>Agent</th><th>Attendance</th><th class="num">On/Late/Abs</th><th class="num">Coverage</th><th></th></tr></thead><tbody>`;
  d.per_agent.slice().sort((a, b) => b.attendance_pct - a.attendance_pct).forEach((a) => {
    html += `<tr><td>${esc(a.agent)}</td>
      <td><span class="bar-wrap"><span class="bar" style="width:${Math.max(a.attendance_pct, 1.5)}%;background:${barColor(a.attendance_pct)}"></span></span>
      <span class="num"> ${a.attendance_pct}%</span></td>
      <td class="num">${a.on_time}/${a.late}/${a.absent}</td>
      <td class="num">${a.coverage_pct}%</td>
      <td><span class="tag">${esc(a.note)}</span></td></tr>`;
  });
  html += `</tbody></table>`;

  // grid
  html += `<h2>Day-by-day</h2><div class="grid-table"><table><thead><tr><th>Agent</th>`;
  d.grid.dates.forEach((dt) => html += `<th>${esc(dt.slice(5))}</th>`);
  html += `</tr></thead><tbody>`;
  const ab = { on_time: "P", late: "L", absent: "A", excused: "E", off: "·", "": "" };
  d.grid.rows.forEach((r) => {
    html += `<tr><td>${esc(r.agent)}</td>`;
    r.cells.forEach((c) => html += `<td class="cell s-${c || "off"}">${ab[c] ?? ""}</td>`);
    html += `</tr>`;
  });
  html += `</tbody></table></div>
    <p class="note">P = on time · L = late · A = absent · E = leave/comp-off · · = off</p>`;
  $("att-results").innerHTML = html;
}

// ---------------- Roster scheduling ----------------
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

  const data = await postForm("/api/roster/recommend", fd, $("ros-status"), $("ros-run"));
  if (data) renderRoster(data);
});

function download(name, text) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  a.download = name; a.click();
}

function renderRoster(d) {
  const cov = Object.entries(d.coverage).map(([k, v]) => `${k} ${v}`).join(" · ");
  let html = `<p class="note">Recommended for <b>${esc(d.month)}</b> · coverage ${esc(cov)} ·
    fairness spread avg ${d.avg_spread}, max ${d.max_spread} (lower = more even)</p>`;

  const shifts = ["Night", "Afternoon", "Morning"];
  html += `<div class="shift-cols">`;
  shifts.forEach((sh) => {
    const rows = d.allocation.filter((r) => r.shift === sh);
    html += `<div class="card shift-col"><h3>${sh} <span class="muted">${rows.length}</span></h3>`;
    rows.forEach((r) => html += `<div class="row">${esc(r.agent)} <span class="muted">· ${esc(r.days)}${r.fixed ? " · fixed" : ""}</span></div>`);
    html += `</div>`;
  });
  html += `</div>`;

  html += `<h2>History rows to append (month, agent, shift)</h2>
    <button class="secondary" id="dl-hist">⬇️ history CSV</button>
    <button class="secondary" id="dl-alloc">⬇️ allocation CSV</button>
    <pre class="csv">${esc(d.history_csv)}</pre>`;
  $("ros-results").innerHTML = html;
  $("dl-hist").onclick = () => download("shift_history_append.csv", d.history_csv);
  $("dl-alloc").onclick = () => download("roster_" + d.month + ".csv", d.allocation_csv);
}
