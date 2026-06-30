"""Central configuration for the attendance dashboard.

All values can be overridden via environment variables (a local .env is loaded
automatically). Defaults are tuned for a single-timezone support team running a
local prototype.
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

try:
    from dotenv import load_dotenv

    # Load the .env next to this file, regardless of the current working dir.
    load_dotenv(BASE_DIR / ".env")
except Exception:  # pragma: no cover - dotenv is optional at runtime
    pass

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# --- Storage -----------------------------------------------------------------
DB_PATH = Path(os.getenv("ATTENDANCE_DB", DATA_DIR / "attendance.db"))

# --- Timezone ----------------------------------------------------------------
# Single timezone for the whole team. Change to your team's zone.
APP_TZ = os.getenv("APP_TZ", "Asia/Kolkata")

# Timezone the Zendesk Explore export timestamps are expressed in.
# Explore commonly exports in the account's zone (often the same as the team's).
# If attendance looks shifted, switch this (e.g. to UTC) in the sidebar.
EXPORT_TZ = os.getenv("EXPORT_TZ", "Asia/Kolkata")

# --- Attendance rules --------------------------------------------------------
# Minutes after shift start still counted as "on time".
GRACE_MIN = int(os.getenv("GRACE_MIN", "10"))

# Agents are expected to mark offline this many minutes before shift end, so
# coverage is measured against [shift_start, shift_end - this]. Leaving on time
# therefore counts as full coverage.
SHIFT_END_BUFFER_MIN = int(os.getenv("SHIFT_END_BUFFER_MIN", "15"))

# Coverage below this fraction of the scheduled shift is flagged as under-hours.
UNDER_HOURS_THRESHOLD = float(os.getenv("UNDER_HOURS_THRESHOLD", "0.85"))

# Zendesk statuses that count as "present / attending".
PRESENT_STATES = [
    s.strip().lower()
    for s in os.getenv("PRESENT_STATES", "online,away").split(",")
    if s.strip()
]

# Back-office agents (e.g. abuse analysts) who work while hidden from the chat
# queue and only ever show "Invisible" on Zendesk. For these agents (matched as
# a case-insensitive substring of the Zendesk name) Invisible also counts as
# present. They should still be marking Online — this just avoids flagging them
# absent in the meantime.
INVISIBLE_AS_PRESENT_AGENTS = [
    s.strip().lower()
    for s in os.getenv("INVISIBLE_AS_PRESENT_AGENTS", "Piyush,Suhaib").split(",")
    if s.strip()
]

# --- Google Sheets roster ----------------------------------------------------
# Default to the team's link-readable roster sheet so the deployed app works
# without any secrets. Override via env / Streamlit secrets if needed.
GOOGLE_SHEETS_ID = os.getenv(
    "GOOGLE_SHEETS_ID", "1N5zqD9SUPRzuHVHQgAVj1L7KnfKQVOdCqF2Wa4h6dkw")
# Numeric gid of the worksheet tab (the gid=... in the sheet URL).
GOOGLE_SHEETS_GID = os.getenv("GOOGLE_SHEETS_GID", "0")
# Worksheet/tab name inside the spreadsheet (blank = first sheet). Used only
# with a service account.
GOOGLE_SHEETS_TAB = os.getenv("GOOGLE_SHEETS_TAB", "")
# Path to the service-account JSON key file. If it doesn't exist, we fall back
# to the public CSV export URL (works when the sheet is link-shared).
GOOGLE_SA_JSON = os.getenv("GOOGLE_SA_JSON", str(BASE_DIR / "service_account.json"))

# Offline fallback roster CSV (used when Sheets isn't configured / for dev).
SAMPLE_ROSTER_CSV = DATA_DIR / "sample_roster.csv"
SAMPLE_EXPLORE_CSV = DATA_DIR / "sample_explore.csv"

# Optional: path to a Zendesk export to auto-load on startup (so the dashboard
# populates without a manual upload). Leave blank to require an upload.
ZENDESK_EXPORT_PATH = os.getenv("ZENDESK_EXPORT_PATH", "")
# Preferred channel for the auto-load (per-agent fallback to other channels).
PREFERRED_CHANNEL = os.getenv("PREFERRED_CHANNEL", "Unified")

# --- Roster layout -----------------------------------------------------------
# The roster's top block is a set of paired columns: an "Agent Name" column
# followed by a working-week column whose HEADER is the day range (e.g.
# "Sun-Thurs", "Mon-Fri") and whose cells hold the daily timing
# (e.g. "6:30AM - 3:30PM", "10PM - 7AM"). Each agent works that timing on every
# day in their range and is OFF on the rest. The parser auto-detects these pairs
# by scanning for "Agent Name" header cells, so no column config is required.
ROSTER_NAME_HEADER = os.getenv("ROSTER_NAME_HEADER", "Agent Name")

# --- Automated roster scheduler ---------------------------------------------
# Date the rotation is anchored to (period 0). Timing rotates every
# SCHEDULE_PERIOD_MONTHS; with 1, timing changes monthly and the working-days
# pattern advances after a full 3-timing cycle (= every 3 months).
SCHEDULE_ANCHOR = os.getenv("SCHEDULE_ANCHOR", "2026-01-01")
SCHEDULE_PERIOD_MONTHS = int(os.getenv("SCHEDULE_PERIOD_MONTHS", "1"))

# Shift timings: (start_h, start_m, end_h, end_m) in app TZ. Night is overnight.
SHIFT_TIMINGS = {
    "Night": (22, 0, 7, 0),
    "Afternoon": (14, 0, 23, 0),
    "Morning": (6, 30, 15, 30),
}
SHIFT_CHRONOLOGY = ["Night", "Afternoon", "Morning"]
# Day-weeks rotating agents use (Mon-Fri is reserved for abuse analysts only).
SCHEDULE_DAY_PATTERNS = ["Sun-Thurs", "Tues-Sat"]

# Target roster structure: headcount per (shift, day-week). Default 4/6/6 split
# across the two day-weeks (= 16 rotating agents). Configurable per request.
TARGET_GRID = {
    "Morning":   {"Sun-Thurs": 2, "Tues-Sat": 2},
    "Afternoon": {"Sun-Thurs": 3, "Tues-Sat": 3},
    "Night":     {"Sun-Thurs": 3, "Tues-Sat": 3},
}

# Abuse analysts kept on fixed timings, excluded from rotation: name -> timing.
SCHEDULE_FIXED_AGENTS = {
    "Suhaib": (8, 0, 17, 0),    # 8AM - 5PM
    "Piyush": (13, 0, 22, 0),   # 1PM - 10PM
}
SCHEDULE_FIXED_PATTERN = os.getenv("SCHEDULE_FIXED_PATTERN", "Mon-Fri")

# --- Web app: access + storage ----------------------------------------------
# Shared password gate. Empty = open (no gate). Set on the host to enable.
SHARE_PASSWORD = os.getenv("SHARE_PASSWORD", "")
# Vercel Blob token; when set, uploads are archived to Blob, else to data/uploads.
BLOB_TOKEN = os.getenv("BLOB_READ_WRITE_TOKEN", "")
UPLOADS_DIR = DATA_DIR / "uploads"
