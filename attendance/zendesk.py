"""Parse a Zendesk Explore agent-state export into normalized status intervals.

Real exports vary a lot. This handles the "Total time in state" shape:
semicolon-delimited, a separate Date column plus time-only Start/End columns,
a Channel column (the **Unified** channel is the agent's overall omnichannel
status — the best signal for "did they mark themselves online"), aggregate
"SUM" rows to drop, and overnight intervals that cross midnight. It also still
handles the simpler shape (one full timestamp per start/end).

Column names are supplied via a mapping (resolved in the UI). Output is a tidy
frame of intervals with timezone-aware start/end in the app timezone.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import IO, List, Optional, Union

import pandas as pd

import config

PathOrBuffer = Union[str, IO]


@dataclass
class ExploreMapping:
    """Maps the user's export columns onto our canonical fields."""

    agent: str
    status: str
    start: str
    end: Optional[str] = None       # full timestamp or time-of-day end
    duration: Optional[str] = None  # fallback if there's no end column
    date: Optional[str] = None      # separate date column (start/end are times)
    channel: Optional[str] = None   # optional channel column to filter on


_AGENT_HINTS = ("agent", "name", "user", "email")
_STATUS_HINTS = ("state", "status", "availability")
_START_HINTS = ("start", "begin", "from")
_END_HINTS = ("end", "finish", "until")
_DURATION_HINTS = ("duration", "elapsed", "seconds")
_DATE_HINTS = ("date", "day")
_CHANNEL_HINTS = ("channel",)

# Date formats tried for the separate Date column (e.g. "30 May 26").
_DATE_FMTS = ("%d %b %y", "%d %b %Y", "%d %B %y", "%d %B %Y")


def _guess(columns, hints):
    lowered = {c: c.lower() for c in columns}
    for hint in hints:
        for col, low in lowered.items():
            if hint in low:
                return col
    return None


def suggest_mapping(df: pd.DataFrame) -> ExploreMapping:
    """Best-effort guess of the column mapping for pre-filling the UI."""
    cols = list(df.columns)
    return ExploreMapping(
        agent=_guess(cols, _AGENT_HINTS) or (cols[0] if cols else ""),
        status=_guess(cols, _STATUS_HINTS) or "",
        start=_guess(cols, _START_HINTS) or "",
        end=_guess(cols, _END_HINTS),
        duration=_guess(cols, _DURATION_HINTS),
        date=_guess(cols, _DATE_HINTS),
        channel=_guess(cols, _CHANNEL_HINTS),
    )


def load_explore_csv(source: PathOrBuffer) -> pd.DataFrame:
    """Read the raw export, auto-detecting the delimiter (comma or semicolon)."""
    df = pd.read_csv(source, sep=None, engine="python", dtype=str,
                     keep_default_na=False)
    df = df.dropna(axis=1, how="all")
    df.columns = [str(c).strip() for c in df.columns]
    return df


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------
def _parse_duration(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.strip()
    has_colon = s.str.contains(":", regex=False)
    out = pd.Series(pd.NaT, index=s.index, dtype="timedelta64[ns]")
    if has_colon.any():
        out.loc[has_colon] = pd.to_timedelta(s[has_colon], errors="coerce")
    if (~has_colon).any():
        secs = pd.to_numeric(s[~has_colon], errors="coerce")
        out.loc[~has_colon] = pd.to_timedelta(secs, unit="s")
    return out


def _parse_datetime(date_s: Optional[pd.Series], time_s: pd.Series) -> pd.Series:
    """Parse start/end. If a date column is given, combine date + time."""
    if date_s is None:
        return pd.to_datetime(time_s, errors="coerce")
    combined = date_s.str.strip() + " " + time_s.str.strip()
    parsed = pd.to_datetime(combined, errors="coerce")
    if parsed.isna().mean() > 0.5:  # ambiguous format — try explicit ones
        for fmt in _DATE_FMTS:
            parsed = pd.to_datetime(combined, format=f"{fmt} %H:%M:%S",
                                    errors="coerce")
            if parsed.isna().mean() <= 0.5:
                break
    return parsed


def _normalize_state(series: pd.Series) -> pd.Series:
    """Lowercase and strip the 'Unified ' prefix so states match PRESENT_STATES."""
    s = series.astype(str).str.strip().str.lower()
    return s.str.replace(r"^unified\s+", "", regex=True)


def normalize_intervals(
    df: pd.DataFrame,
    mapping: ExploreMapping,
    export_tz: Optional[str] = None,
) -> pd.DataFrame:
    """Return columns: agent, channel, status, present, start_ts, end_ts.

    Timestamps are tz-aware in the app timezone. Aggregate 'SUM' rows are
    dropped. All channels are kept; channel selection happens in
    merge_present_intervals so we can fall back per agent.
    """
    export_tz = export_tz or config.EXPORT_TZ
    work = df.copy()

    out = pd.DataFrame()
    out["agent"] = work[mapping.agent].astype(str).str.strip()
    out["status"] = _normalize_state(work[mapping.status])
    out["channel"] = (work[mapping.channel].astype(str).str.strip()
                      if mapping.channel else "")

    # Drop aggregate / header artifacts.
    keep = ~out["status"].isin(["sum", "state", ""]) & (out["agent"].str.lower() != "sum")
    out, work = out[keep], work[keep]

    date_s = work[mapping.date].astype(str) if mapping.date else None
    start = _parse_datetime(date_s, work[mapping.start].astype(str))

    if mapping.end:
        end = _parse_datetime(date_s, work[mapping.end].astype(str))
        if mapping.date is not None:  # time-only end may cross midnight
            overnight = end < start
            end = end.where(~overnight, end + pd.Timedelta(days=1))
    elif mapping.duration:
        end = start + _parse_duration(work[mapping.duration])
    else:
        raise ValueError("Provide either an end column or a duration column.")

    out["start_ts"] = _localize(start, export_tz)
    out["end_ts"] = _localize(end, export_tz)
    out["present"] = out["status"].isin(config.PRESENT_STATES)

    # Back-office agents who only ever go Invisible: count Invisible as present.
    if config.INVISIBLE_AS_PRESENT_AGENTS:
        names = config.INVISIBLE_AS_PRESENT_AGENTS
        agent_l = out["agent"].str.lower()
        is_backoffice = agent_l.apply(lambda a: any(n in a for n in names))
        out.loc[is_backoffice & (out["status"] == "invisible"), "present"] = True

    out = out.dropna(subset=["start_ts", "end_ts"])
    out = out[out["end_ts"] >= out["start_ts"]]
    return out.sort_values(["agent", "start_ts"]).reset_index(drop=True)


def _merge(pairs) -> List[tuple]:
    """Union a sorted list of (start, end) intervals."""
    out, cur_s, cur_e = [], None, None
    for s, e in pairs:
        if cur_s is None:
            cur_s, cur_e = s, e
        elif s <= cur_e:
            cur_e = max(cur_e, e)
        else:
            out.append((cur_s, cur_e))
            cur_s, cur_e = s, e
    if cur_s is not None:
        out.append((cur_s, cur_e))
    return out


def merge_present_intervals(norm: pd.DataFrame,
                            preferred_channel: Optional[str] = None) -> pd.DataFrame:
    """Union overlapping/adjacent present intervals per agent.

    Collapses duplicate coverage (e.g. across channels) so it isn't double
    counted. If preferred_channel is given (e.g. 'Unified', the agent's overall
    status), an agent's intervals come from that channel when they have any
    there; agents with no rows on that channel fall back to the union of all
    their channels (handles e.g. chat-only agents with no Unified status).
    Returns agent, status='present', start_ts, end_ts, present=True.
    """
    present = norm[norm["present"]]
    rows: List[tuple] = []
    for agent, g in present.groupby("agent"):
        if preferred_channel and "channel" in g.columns:
            pref = g[g["channel"] == preferred_channel]
            g = pref if not pref.empty else g
        g = g.sort_values("start_ts")
        for s, e in _merge(list(zip(g["start_ts"], g["end_ts"]))):
            rows.append((agent, s, e))
    out = pd.DataFrame(rows, columns=["agent", "start_ts", "end_ts"])
    out["status"] = "present"
    out["present"] = True
    return out


def _localize(series: pd.Series, export_tz: str) -> pd.Series:
    if series.dt.tz is None:
        series = series.dt.tz_localize(export_tz, ambiguous="NaT",
                                       nonexistent="NaT")
    return series.dt.tz_convert(config.APP_TZ)
