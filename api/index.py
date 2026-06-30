"""FastAPI backend for the attendance + roster-scheduling web app.

Reuses the Python core (roster, zendesk, engine, identity, scheduling, storage).
Runs as a Vercel Python function; also `uvicorn api.index:app` locally.
"""
from __future__ import annotations

import io
import json
import os
import sys

import pandas as pd
from fastapi import FastAPI, File, Form, Header, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from attendance import engine, identity, roster, scheduling, storage, zendesk  # noqa: E402

app = FastAPI(title="Roster & Attendance")
_PUBLIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "public")


# ---------------------------------------------------------------------------
# Auth + helpers
# ---------------------------------------------------------------------------
def _auth(pw):
    if config.SHARE_PASSWORD and (pw or "") != config.SHARE_PASSWORD:
        raise HTTPException(401, "Incorrect or missing password.")


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


@app.get("/api/config")
def app_config():
    return {"auth_required": bool(config.SHARE_PASSWORD), "blob": storage.using_blob()}


@app.post("/api/attendance")
async def attendance(
    zendesk_file: UploadFile = File(...),
    roster_url: str = Form(""),
    roster_file: UploadFile = File(None),
    export_tz: str = Form("Asia/Kolkata"),
    x_app_password: str = Header(None),
):
    _auth(x_app_password)
    grid = await _grid_from(roster_url, roster_file)
    patterns = roster.parse_patterns(grid)
    if patterns.empty:
        raise HTTPException(400, "No shift patterns parsed from the roster sheet.")
    leaves = roster.parse_leaves(grid)

    zbytes = await zendesk_file.read()
    try:
        raw = zendesk.load_explore_csv(io.BytesIO(zbytes))
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

    g = att.pivot_table(index="agent", columns="date", values="status",
                        aggfunc="first").fillna("")
    grid_out = {"dates": [str(c) for c in g.columns],
                "rows": [{"agent": ag, "cells": [g.loc[ag, c] for c in g.columns]}
                         for ag in g.index]}
    summary = engine.summarize(att)
    per_agent = _per_agent(att)

    # Archive the inputs + summary for later analysis.
    try:
        storage.save_bytes("zendesk", zendesk_file.filename or "export.csv", zbytes, "text/csv")
        storage.save_text("roster", "roster_grid.csv", grid.to_csv(index=False, header=False))
        storage.save_json("attendance_summary", "summary.json",
                          {"range": [str(ex.min()), str(ex.max())],
                           "summary": summary, "per_agent": per_agent})
    except Exception:
        pass

    return {"range": [str(ex.min()), str(ex.max())], "summary": summary,
            "per_agent": per_agent, "grid": grid_out,
            "unmapped": identity.unmapped_agents(
                sorted(patterns["agent"].unique()), zagents, amap)}


@app.post("/api/roster/recommend")
async def recommend(
    roster_url: str = Form(""),
    roster_file: UploadFile = File(None),
    history_url: str = Form(""),
    history_file: UploadFile = File(None),
    month: str = Form(""),
    targets: str = Form(""),
    x_app_password: str = Header(None),
):
    _auth(x_app_password)
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

    tgt = json.loads(targets) if targets.strip() else None
    rec = scheduling.recommend(current, history, month or None, tgt)
    alloc = rec["allocation"]

    try:
        storage.save_text("roster_recommend", f"roster_{rec['month']}.csv",
                          alloc.to_csv(index=False))
        storage.save_text("history_rows", f"history_{rec['month']}.csv",
                          rec["history_rows"].to_csv(index=False))
    except Exception:
        pass

    return {
        "month": rec["month"], "coverage": rec["coverage"],
        "avg_shift_spread": rec["avg_shift_spread"],
        "max_shift_spread": rec["max_shift_spread"],
        "avg_day_spread": rec["avg_day_spread"],
        "unfilled_slots": rec["unfilled_slots"],
        "unassigned_agents": rec["unassigned_agents"],
        "current": current.to_dict(orient="records"),
        "allocation": alloc.to_dict(orient="records"),
        "history_rows": rec["history_rows"].to_dict(orient="records"),
        "history_csv": rec["history_rows"].to_csv(index=False),
        "allocation_csv": alloc.to_csv(index=False),
    }


@app.post("/api/roster/history")
async def roster_history(
    roster_url: str = Form(""),
    history_file: UploadFile = File(None),
    x_app_password: str = Header(None),
):
    _auth(x_app_password)
    try:
        if roster_url:
            df = scheduling.load_history(url=roster_url)
        elif history_file is not None:
            df = scheduling.load_history(data=await history_file.read())
        else:
            raise HTTPException(400, "Provide a history link or file.")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Could not parse history: {exc}")
    months = sorted(df["month"].unique().tolist())
    return {"months": months, "records": df.to_dict(orient="records")}


@app.get("/api/records")
def records(x_app_password: str = Header(None)):
    _auth(x_app_password)
    return {"records": storage.list_records()}


@app.get("/api/download")
def download(key: str, x_app_password: str = Header(None)):
    _auth(x_app_password)
    try:
        data = storage.read_local(key)
    except Exception:
        raise HTTPException(404, "Not found")
    return Response(content=data, media_type="application/octet-stream",
                    headers={"content-disposition": f'attachment; filename="{os.path.basename(key)}"'})


# Static front-end (local dev convenience; on Vercel public/ is served directly).
if os.path.isdir(_PUBLIC):
    from fastapi.staticfiles import StaticFiles

    @app.get("/")
    def _index():
        return FileResponse(os.path.join(_PUBLIC, "index.html"))

    app.mount("/", StaticFiles(directory=_PUBLIC), name="static")
