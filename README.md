# Support Team Attendance Dashboard

Compares each support agent's **scheduled shift** (from a Google Sheets roster)
against their **actual online time** in Zendesk (from a Zendesk Explore export),
and shows who was on time, late, or absent — plus how much of each shift was
actually covered.

The dashboard has two tabs: **📊 Attendance** (roster vs. actual online time) and
**📅 Roster scheduling** (auto-generated monthly shift rotation).

## How it works

```
Google Sheets (roster)  ──┐
                          ├─► reconciliation engine ─► SQLite ─► Streamlit dashboard
Zendesk Explore CSV     ──┘
```

- **Roster** is the top block of the sheet: side-by-side `Agent Name | <day-range>`
  column pairs, where the header (`Sun-Thurs`, `Tues-Sat`, `Mon-Fri`, …) is the
  agent's working week and each cell holds the daily timing (`6:30AM - 3:30PM`,
  `10PM - 7AM`, …). Each agent works that timing on every day in their range and
  is OFF on the rest. It's a recurring weekly pattern (no fixed dates), so it's
  expanded across whatever date range the Zendesk export covers. Extra agent-name
  pairs (e.g. the "Abuse analysts" sub-block) are picked up automatically.
- **Zendesk** status history comes from an **Explore** export (agent state over
  time). Zendesk only exposes *current* status via API, so attendance history is
  captured by exporting from Explore and uploading the CSV here.
- **Rules:** a single team timezone; online within the grace period (default
  10 min) of shift start = *on time*, later = *late*, never online during the
  shift = *absent*. Both **Online** and **Away** count as present. (All
  configurable in `config.py` / `.env`.)

## Quick start (prototype, sample data)

```bash
cd support-attendance
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

In the sidebar: click **Load / refresh roster** (uses `data/sample_roster.csv`
when no Google Sheet is configured), tick **Use sample export**, then click
**Apply mapping**. The dashboard renders KPIs, the per-agent×day grid, the daily
trend, and the agent drill-down.

Run the tests with `pytest`.

## Deploy to Streamlit Community Cloud

This is a Streamlit app, so it deploys to **Streamlit Community Cloud** (free) —
**not Vercel**, which can't run a long-lived Streamlit/WebSocket server.

1. Push this folder to a **private** GitHub repo (repo root = this folder, so
   `app.py` is at the top).
2. Go to [share.streamlit.io](https://share.streamlit.io) → **New app** → pick the
   repo/branch, set **Main file path** to `app.py`, and deploy.
3. The roster sheet ID is defaulted in `config.py` (the sheet is link-readable),
   so it runs with no secrets. To override, add to the app's **Secrets**:
   `GOOGLE_SHEETS_ID`, `GOOGLE_SHEETS_GID`, `EXPORT_TZ` (Streamlit secrets are also
   exposed as env vars, which `config.py` reads).
4. **Access control:** in the app's settings set it to **private** and invite
   viewers by Google email — attendance data is sensitive.

Notes: on the cloud there's no local Zendesk file, so upload the Explore export in
the sidebar each session; SQLite is ephemeral (resets on restart) — fine for this
prototype. Other container hosts (Render, Railway, Fly.io) also work via the same
`requirements.txt`.

## Connecting your real data

### 1. Google Sheets roster

**Option A — public/link-shared sheet (simplest):** copy `.env.example` to `.env`,
set `GOOGLE_SHEETS_ID` and `GOOGLE_SHEETS_GID` from the sheet URL. If no service
account key is present, the app reads the sheet's public CSV export URL directly —
no credentials needed (works when the sheet is shared as "anyone with the link").

**Option B — service account (private sheet):**

1. Create a Google Cloud **service account** and download its JSON key.
   (Google Cloud Console → IAM & Admin → Service Accounts → Keys → Add key.)
   Enable the **Google Sheets API** for the project.
2. Save the key as `service_account.json` in this folder (or point
   `GOOGLE_SA_JSON` at it).
3. **Share the spreadsheet** with the service account's email
   (`...@...iam.gserviceaccount.com`) as a Viewer.
4. Set `GOOGLE_SHEETS_ID` (and `GOOGLE_SHEETS_TAB` if not the first tab) in `.env`.

### 2. Zendesk Explore export

Export the agent **state/activity over time** report as CSV and upload it in the
sidebar. The app auto-detects the delimiter (comma or semicolon) and pre-guesses
the column mapping; adjust if needed. It handles the "Total time in state" shape:

- A separate **Date** column plus time-only **Start/End** columns (or a single
  full timestamp), or a **Duration** column instead of an end.
- A **Channel** column. Pick a **preferred channel** — use **Unified** (the
  agent's overall omnichannel status, i.e. what they actually toggle). Agents with
  no rows on that channel automatically fall back to the union of their other
  channels (e.g. chat-only agents who never set a Unified status).
- States are normalized (the `Unified ` prefix is stripped, lowercased), so
  `Unified online`/`Online` both count via `PRESENT_STATES`. Aggregate `SUM` rows
  are dropped, and intervals crossing midnight are handled.
- Overlapping intervals are merged per agent so coverage isn't double-counted.

Set the **Export timezone** in the sidebar (or `EXPORT_TZ` in `.env`). For this
team it's `Asia/Kolkata`; if attendance times look shifted, switch it.

## Configuration

All settings live in `config.py` and can be overridden via environment variables
(see `.env.example`): timezones (`APP_TZ`, `EXPORT_TZ`), `GRACE_MIN`,
`UNDER_HOURS_THRESHOLD`, `PRESENT_STATES`, and the roster layout columns.

## Project layout

| Path | Purpose |
|------|---------|
| `app.py` | Streamlit UI |
| `config.py` | Central config (env-overridable) |
| `attendance/roster.py` | Read + normalize the Google Sheets roster |
| `attendance/zendesk.py` | Parse the Explore CSV into status intervals |
| `attendance/identity.py` | Map roster names ↔ Zendesk agents |
| `attendance/engine.py` | Reconcile shifts vs. presence → status/metrics |
| `attendance/store.py` | SQLite persistence |
| `tests/` | Engine + parser unit tests |
| `data/` | Sample CSVs + generated SQLite DB |

## Notes / limitations (prototype)

- Breaks/lunch are counted as present if the agent stays Online/Away.
- **Leaves / comp-offs** are read from the dated bottom block (the stacked weekly
  tables) and shown as *excused* (blue), excluded from attendance %, so an agent
  on approved leave is not counted absent. Matching is by month/day (the headers
  have no year).
- **End-of-shift buffer:** per the sheet's "mark offline 15 min before shift end"
  rule, coverage is measured against `[shift_start, shift_end − 15 min]` (the
  *expected* window), so leaving on time counts as full coverage. Configurable via
  `SHIFT_END_BUFFER_MIN`. The drill-down shows `expected_minutes` vs
  `covered_minutes`.
- If the same agent is listed under more than one shift pattern, the dashboard
  flags it (data-quality warning) and evaluates all listed shifts.
- No authentication or hosting yet — runs locally.
- Per-agent timezones are not supported (single team timezone by design).
