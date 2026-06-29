# Support Roster & Attendance

A small web app (FastAPI + static HTML, **no Streamlit**) with two tabs:

- **📊 Attendance** — compares each agent's scheduled shift (Google Sheets roster)
  against their actual online time (Zendesk Explore export): on time / late /
  absent + coverage.
- **📅 Roster scheduling** — recommends next month's shift allocation using a
  **fairness** assessment over the full shift history (keeps each agent's lifetime
  Night/Afternoon/Morning mix as even as possible) while meeting per-shift
  headcounts.

The parsing + reconciliation + scheduling logic lives in plain, tested Python
modules under `attendance/`; the web layer (`api/` + `public/`) is a thin skin.

## How it works

```
Google Sheets (roster + shift history) ─┐
                                        ├─► attendance/ core (pandas) ─► JSON ─► HTML UI
Zendesk Explore CSV (upload) ───────────┘
```

- **Roster** is the sheet's top block: `Agent Name | <day-range>` column pairs
  where the header (`Sun-Thurs`, `Tues-Sat`, `Mon-Fri`) is the working week and
  each cell is the daily timing (`6:30AM - 3:30PM`, `10PM - 7AM`). Leaves/comp-offs
  in the dated lower block are read too (shown as *excused*).
- **Zendesk**: the "Total time in state" Explore export (semicolon CSV; Date +
  Start/End columns; `Unified` channel = the agent's overall status). Online + Away
  count as present; for back-office agents (`config.INVISIBLE_AS_PRESENT_AGENTS`)
  Invisible also counts.
- **Attendance rules**: single timezone; online within the grace window
  (default 10 min) = on time, later = late, never = absent; coverage measured to
  15 min before shift end. All in `config.py`.
- **Scheduling**: reads the current allocation from the sheet + a **shift-history**
  source (`month, agent, shift`), then assigns next month to balance everyone's
  lifetime shift mix (capacity-preserving + 2-opt). Outputs rows to append to the
  history tab + a CSV.

## Quick start (local)

```bash
cd support-attendance
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn api.index:app --reload --port 8000
# open http://localhost:8000
```

- **Attendance tab:** paste the roster sheet link (or upload a roster CSV) + upload
  the Zendesk Explore export → **Map attendance**.
- **Roster tab:** paste the current roster link (or CSV) + optional history link/CSV
  → **Recommend next roster**, then download the history rows to append.

Run tests with `pytest` (26 tests).

## Shift-history tab

Create a tab/sheet with columns `month, agent, shift` (e.g. `2026-06, Ashish,
Night`). The app reads it to assess fairness and outputs the new month's rows to
paste back in. Without history it bootstraps from the current allocation. (See
`data/sample_history.csv`.) Writing back automatically would need a Google service
account — out of scope; paste the rows for now.

## Deploy to Vercel

This is a plain web app, so it runs on Vercel (unlike Streamlit).

1. Push to GitHub (already wired to `maulikd24/Roster-Management`).
2. On vercel.com → **New Project** → import the repo. `vercel.json` builds the
   Python API (`api/index.py`) and serves `public/` statically; no config needed.
3. The roster sheet ID is defaulted in `config.py` (link-readable sheet), so it
   runs with no secrets. Override via Vercel **Environment Variables**
   (`GOOGLE_SHEETS_ID`, etc.) if needed.

Notes: pandas cold-start + 10s Hobby function limit (fine for ~16k rows). **Add
access control** — attendance data is sensitive (Vercel Pro password protection,
or a shared-password gate). The same code also runs on Render/Railway (`uvicorn
api.index:app`).

## Layout

| Path | Purpose |
|------|---------|
| `api/index.py` | FastAPI endpoints (`/api/attendance`, `/api/roster/recommend`) |
| `public/` | Static UI (`index.html`, `app.js`, `styles.css`) |
| `attendance/roster.py` | Read/parse the sheet (URL or CSV) |
| `attendance/zendesk.py` | Parse the Explore export → status intervals |
| `attendance/engine.py` | Reconcile shifts vs. presence → metrics |
| `attendance/identity.py` | Map roster names ↔ Zendesk agents |
| `attendance/scheduling.py` | Fairness-based next-month roster |
| `config.py` | Timezone, rules, shift defaults |
| `tests/` | pytest suite |
| `vercel.json` | Vercel build/routing |
