"""FastAPI backend for the attendance + roster-scheduling web app.

Reuses the Python core (roster, zendesk, engine, identity, scheduling). Two JSON
endpoints power a static HTML front-end. Designed to run as a Vercel Python
serverless function; also runnable locally with `uvicorn api.index:app`.
"""
from __future__ import annotations

import io
import os
import sys

import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from attendance import engine, identity, roster, scheduling, zendesk  # noqa: E402

app = FastAPI(title="Roster & Attendance")

_PUBLIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "public")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _grid_from(roster_url: str, roster_file: UploadFile):
    if roster_url:
        try:
            return roster.read_grid_from_url(roster_url)
        except Exception as exc:
            raise HTTPException(400, f"Could not read roster sheet: {exc}")
    if roster_file is not None:
        return roster.read_grid_from_csv(await roster_file.read())
    raise HTTPException(400, "Provide a roster sheet link or upload a roster CSV.")


def _per_agent(att: pd.DataFrame) -> list:
    backoffice = config.INVISIBLE_AS_PRESENT_AGENTS
    out = []
    for ag in sorted(att["agent"].unique()):
        a = att[att["agent"] == ag]
        sched = int(a["status"].isin([engine.ON_TIME, engine.LATE, engine.ABSENT]).sum())
        ont = int((a["status"] == engine.ON_TIME).sum())
        late = int((a["status"] == engine.LATE).sum())
        absent = int((a["status"] == engine.ABSENT).sum())
        excused = int((a["status"] == engine.EXCUSED).sum())
        present = ont + late
        cov = a[a["status"].isin([engine.ON_TIME, engine.LATE])]["coverage_pct"].mean()
        if sched > 0 and present == 0 and absent == sched:
            note = "no Zendesk data"
        elif any(n in ag.lower() for n in backoffice):
            note = "via Invisible"
        else:
            note = ""
        out.append(dict(
            agent=ag, scheduled=sched, on_time=ont, late=late, absent=absent,
            excused=excused,
            attendance_pct=round(100 * present / sched, 1) if sched else 0.0,
            coverage_pct=round(100 * cov, 1) if pd.notna(cov) else 0.0,
            note=note,
        ))
    return out


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/attendance")
async def attendance(
    zendesk_file: UploadFile = File(...),
    roster_url: str = Form(""),
    roster_file: UploadFile = File(None),
    export_tz: str = Form("Asia/Kolkata"),
):
    grid = await _grid_from(roster_url, roster_file)
    patterns = roster.parse_patterns(grid)
    if patterns.empty:
        raise HTTPException(400, "No shift patterns parsed from the roster sheet.")
    leaves = roster.parse_leaves(grid)

    try:
        raw = zendesk.load_explore_csv(io.BytesIO(await zendesk_file.read()))
        mapping = zendesk.suggest_mapping(raw)
        norm = zendesk.normalize_intervals(raw, mapping, export_tz)
    except Exception as exc:
        raise HTTPException(400, f"Could not parse Zendesk export: {exc}")
    if norm.empty:
        raise HTTPException(400, "No usable rows in the Zendesk export.")

    pref = config.PREFERRED_CHANNEL if mapping.channel else None
    merged = zendesk.merge_present_intervals(norm, pref)

    zagents = sorted(norm["agent"].unique())
    amap = identity.default_map(sorted(patterns["agent"].unique()), zagents)

    ex = norm["start_ts"].dt.date
    dates = roster.date_span(ex.min(), ex.max())
    roster_df = roster.expand(patterns, dates, leaves)
    att = engine.compute_attendance(identity.attach_zendesk_names(roster_df, amap), merged)

    # Status grid (agent x date)
    g = att.pivot_table(index="agent", columns="date", values="status",
                        aggfunc="first").fillna("")
    grid_dates = [str(c) for c in g.columns]
    grid_rows = [{"agent": ag, "cells": [g.loc[ag, c] for c in g.columns]}
                 for ag in g.index]

    return {
        "range": [str(ex.min()), str(ex.max())],
        "summary": engine.summarize(att),
        "per_agent": _per_agent(att),
        "grid": {"dates": grid_dates, "rows": grid_rows},
        "unmapped": identity.unmapped_agents(
            sorted(patterns["agent"].unique()), zagents, amap),
    }


@app.post("/api/roster/recommend")
async def recommend(
    roster_url: str = Form(""),
    roster_file: UploadFile = File(None),
    history_url: str = Form(""),
    history_file: UploadFile = File(None),
    month: str = Form(""),
):
    grid = await _grid_from(roster_url, roster_file)
    current = scheduling.current_allocation(grid)
    if current.empty:
        raise HTTPException(400, "No current shift allocation found in the sheet.")
    try:
        if history_url:
            history = scheduling.load_history(url=history_url)
        elif history_file is not None:
            history = scheduling.load_history(data=await history_file.read())
        else:
            history = scheduling.load_history()
    except Exception as exc:
        raise HTTPException(400, f"Could not read history: {exc}")

    rec = scheduling.recommend(current, history, month or None)
    alloc = rec["allocation"]
    return {
        "month": rec["month"],
        "coverage": rec["coverage"],
        "avg_spread": rec["avg_spread"],
        "max_spread": rec["max_spread"],
        "current": current.to_dict(orient="records"),
        "allocation": alloc.to_dict(orient="records"),
        "history_rows": rec["history_rows"].to_dict(orient="records"),
        "history_csv": rec["history_rows"].to_csv(index=False),
        "allocation_csv": alloc.to_csv(index=False),
    }


# Serve the static front-end (handy for local dev; on Vercel, public/ is served
# directly and only /api/* hits this function).
if os.path.isdir(_PUBLIC):
    @app.get("/")
    def _index():
        return FileResponse(os.path.join(_PUBLIC, "index.html"))

    app.mount("/", StaticFiles(directory=_PUBLIC), name="static")
