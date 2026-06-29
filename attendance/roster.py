"""Read the roster from Google Sheets and normalize it to per-shift rows.

The roster's top block is a set of side-by-side column pairs:

    Agent Name | Sun-Thurs        Agent Name | Tues-Sat      Agent Name | Mon-Fri
    Ashish     | 6:30AM - 3:30PM  Zaki       | 6:30AM-3:30PM Asjad      | 10PM - 7AM
    ...

The *header* of each second column ("Sun-Thurs", "Mon-Fri", ...) is the agent's
working-week pattern; the cell holds their daily shift timing. Each agent works
that timing on every day in their range and is OFF on the rest. This is a
recurring weekly pattern with no fixed dates, so we expand it across whatever
date range we're evaluating (driven by the Zendesk export).
"""
from __future__ import annotations

import io
import re
from datetime import date, datetime, timedelta
from typing import Dict, List, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

import config

# Monday=0 .. Sunday=6 (matches datetime.weekday()).
_WEEKDAY = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

_TIME = re.compile(
    r"(\d{1,2})(?::(\d{2}))?\s*([ap]m)\s*(?:[-–]|to)\s*"
    r"(\d{1,2})(?::(\d{2}))?\s*([ap]m)",
    re.IGNORECASE,
)

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_DATE_HDR = re.compile(r"([A-Za-z]{3,})\s+(\d{1,2})")


# ---------------------------------------------------------------------------
# Reading raw grid
# ---------------------------------------------------------------------------
def read_roster_dataframe() -> pd.DataFrame:
    """Load the raw sheet grid (no header) from Sheets or the sample CSV.

    Order of preference: service-account gspread → public CSV export URL →
    local sample CSV.
    """
    if config.GOOGLE_SHEETS_ID:
        from pathlib import Path

        if Path(config.GOOGLE_SA_JSON).exists():
            return _read_from_sheets()
        return _read_public_csv()
    if config.SAMPLE_ROSTER_CSV.exists():
        return pd.read_csv(config.SAMPLE_ROSTER_CSV, header=None, dtype=str,
                           keep_default_na=False)
    raise RuntimeError(
        "No roster source: set GOOGLE_SHEETS_ID or provide data/sample_roster.csv"
    )


def _read_from_sheets() -> pd.DataFrame:  # pragma: no cover - requires creds
    import gspread

    gc = gspread.service_account(filename=config.GOOGLE_SA_JSON)
    sh = gc.open_by_key(config.GOOGLE_SHEETS_ID)
    ws = sh.worksheet(config.GOOGLE_SHEETS_TAB) if config.GOOGLE_SHEETS_TAB else sh.sheet1
    return pd.DataFrame(ws.get_all_values()).astype(str)


def _read_public_csv() -> pd.DataFrame:  # pragma: no cover - requires network
    url = (
        f"https://docs.google.com/spreadsheets/d/{config.GOOGLE_SHEETS_ID}"
        f"/export?format=csv&gid={config.GOOGLE_SHEETS_GID}"
    )
    return pd.read_csv(url, header=None, dtype=str, keep_default_na=False)


_SHEET_ID_RE = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]+)")
_GID_RE = re.compile(r"[?#&]gid=(\d+)")


def csv_export_url(sheet_url: str) -> str:
    """Turn any Google Sheets URL into its public CSV export URL (with gid)."""
    m = _SHEET_ID_RE.search(sheet_url)
    if not m:
        raise ValueError("Not a Google Sheets URL (expected /spreadsheets/d/<id>).")
    sheet_id = m.group(1)
    gid_m = _GID_RE.search(sheet_url)
    gid = gid_m.group(1) if gid_m else "0"
    return (f"https://docs.google.com/spreadsheets/d/{sheet_id}"
            f"/export?format=csv&gid={gid}")


def read_grid_from_url(sheet_url: str) -> pd.DataFrame:
    """Read a sheet tab (by full URL) into a header-less grid via public CSV."""
    return pd.read_csv(csv_export_url(sheet_url), header=None, dtype=str,
                       keep_default_na=False)


def read_grid_from_csv(data) -> pd.DataFrame:
    """Read an uploaded CSV (bytes or text) into a header-less grid."""
    buf = io.BytesIO(data) if isinstance(data, (bytes, bytearray)) else io.StringIO(data)
    return pd.read_csv(buf, header=None, dtype=str, keep_default_na=False)


# ---------------------------------------------------------------------------
# Parsing the recurring pattern
# ---------------------------------------------------------------------------
def parse_day_range(label: str) -> List[int]:
    """'Sun-Thurs' -> [6,0,1,2,3]; 'Mon-Fri' -> [0,1,2,3,4]."""
    parts = re.split(r"\s*[-–]\s*", str(label).strip())
    parts = [p for p in (p.strip() for p in parts) if p]
    if len(parts) != 2:
        return []
    try:
        start = _WEEKDAY[parts[0][:3].lower()]
        end = _WEEKDAY[parts[1][:3].lower()]
    except KeyError:
        return []
    days, cur = [], start
    while True:
        days.append(cur)
        if cur == end:
            break
        cur = (cur + 1) % 7
    return days


def parse_timing(cell: str):
    """'6:30AM - 3:30PM' -> (6,30,15,30); returns None if not a time range."""
    m = _TIME.search(str(cell))
    if not m:
        return None
    sh, sm, sap, eh, em, eap = m.groups()
    return (_to_24h(int(sh), sap), int(sm or 0), _to_24h(int(eh), eap), int(em or 0))


def _to_24h(hour: int, ampm: str) -> int:
    ampm = ampm.lower()
    if ampm == "am":
        return 0 if hour == 12 else hour
    return 12 if hour == 12 else hour + 12


def parse_patterns(grid: pd.DataFrame) -> pd.DataFrame:
    """Extract recurring shift patterns from the top roster block.

    Returns columns: agent, pattern (day label), days (list[int]),
    start_h, start_m, end_h, end_m.
    """
    # Bound to the top block: everything above the first fully-blank row.
    blank_rows = grid.index[grid.apply(
        lambda r: all(str(v).strip() == "" for v in r), axis=1)]
    last = int(blank_rows.min()) if len(blank_rows) else len(grid)
    block = grid.iloc[:last]

    name_hdr = config.ROSTER_NAME_HEADER.strip().lower()
    rows: List[dict] = []

    for r in range(len(block)):
        for c in range(block.shape[1] - 1):
            if str(block.iat[r, c]).strip().lower() != name_hdr:
                continue
            day_label = str(block.iat[r, c + 1]).strip()
            days = parse_day_range(day_label)
            if not days:
                continue
            # Read agent/timing pairs in the rows below this header.
            for rr in range(r + 1, len(block)):
                agent = str(block.iat[rr, c]).strip()
                if not agent or agent.lower() == name_hdr:
                    if agent.lower() == name_hdr:
                        break
                    continue
                timing = parse_timing(block.iat[rr, c + 1])
                if timing is None:
                    continue
                sh, sm, eh, em = timing
                rows.append(dict(agent=agent, pattern=day_label, days=days,
                                 start_h=sh, start_m=sm, end_h=eh, end_m=em))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Leaves / comp-offs (the dated bottom block)
# ---------------------------------------------------------------------------
def parse_date_header(text: str):
    """'May 31' -> (5, 31); returns None if not a date header."""
    m = _DATE_HDR.search(str(text))
    if not m:
        return None
    month = _MONTHS.get(m.group(1)[:3].lower())
    return (month, int(m.group(2))) if month else None


def _leave_kind(cell: str):
    low = str(cell).strip().lower()
    if "comp off" in low or "comp-off" in low:
        return "comp_off"
    if low == "leave" or low.startswith("leave"):
        return "leave"
    return None


def _header_columns(grid: pd.DataFrame, r: int):
    """If row r is a 'Name | <dates...>' header, return (name_col, {col: (m,d)})."""
    name_col = None
    for c in range(grid.shape[1]):
        if str(grid.iat[r, c]).strip().lower() == "name":
            name_col = c
            break
    if name_col is None:
        return None
    date_cols = {}
    for c in range(name_col + 1, grid.shape[1]):
        parsed = parse_date_header(grid.iat[r, c])
        if parsed:
            date_cols[c] = parsed
    return (name_col, date_cols) if date_cols else None


def parse_leaves(grid: pd.DataFrame) -> Dict[tuple, str]:
    """Extract leave/comp-off exceptions from the dated bottom block(s).

    The bottom block is a stack of weekly tables, each with its own
    'Shift | Name | <dates...>' header. We track the current header's date
    columns and apply them only to that table's rows. Returns a dict keyed by
    (agent, month, day) -> 'leave' | 'comp_off'. Years aren't in the headers, so
    matching is year-agnostic.
    """
    leaves: Dict[tuple, str] = {}
    name_col = None
    date_cols: Dict[int, tuple] = {}

    for r in range(len(grid)):
        header = _header_columns(grid, r)
        if header is not None:
            name_col, date_cols = header
            continue
        if name_col is None:
            continue
        agent = str(grid.iat[r, name_col]).strip()
        if not agent:
            continue
        for c, (month, day) in date_cols.items():
            kind = _leave_kind(grid.iat[r, c])
            if kind:
                leaves[(agent, month, day)] = kind
    return leaves


# ---------------------------------------------------------------------------
# Expanding the pattern across actual dates
# ---------------------------------------------------------------------------
def expand(patterns: pd.DataFrame, dates: Sequence[date],
           leaves: Dict[tuple, str] = None) -> pd.DataFrame:
    """Expand recurring patterns into one row per (agent, date).

    Output: agent, pattern, date, off, exception, shift_start, shift_end
    (tz-aware APP_TZ). A working day that falls on an approved leave/comp-off is
    marked off with `exception` set so it reads as excused, not absent.
    """
    tz = ZoneInfo(config.APP_TZ)
    leaves = leaves or {}
    rows = []
    for _, p in patterns.iterrows():
        working = set(p["days"])
        for d in dates:
            exception = leaves.get((p["agent"], d.month, d.day))
            if d.weekday() not in working:
                rows.append(dict(agent=p["agent"], pattern=p["pattern"], date=d,
                                 off=True, exception=None,
                                 shift_start=None, shift_end=None))
                continue
            if exception:  # scheduled but on approved leave
                rows.append(dict(agent=p["agent"], pattern=p["pattern"], date=d,
                                 off=True, exception=exception,
                                 shift_start=None, shift_end=None))
                continue
            start = datetime(d.year, d.month, d.day, p["start_h"], p["start_m"], tzinfo=tz)
            end = datetime(d.year, d.month, d.day, p["end_h"], p["end_m"], tzinfo=tz)
            if end <= start:  # overnight shift
                end += timedelta(days=1)
            rows.append(dict(agent=p["agent"], pattern=p["pattern"], date=d,
                             off=False, exception=None,
                             shift_start=start, shift_end=end))
    return pd.DataFrame(rows)


def date_span(start: date, end: date) -> List[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def load_roster(dates: Sequence[date]) -> pd.DataFrame:
    """Convenience: read + parse patterns + leaves + expand across dates."""
    grid = read_roster_dataframe()
    return expand(parse_patterns(grid), dates, parse_leaves(grid))
