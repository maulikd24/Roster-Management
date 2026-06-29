"""Reconcile scheduled shifts against actual Zendesk presence intervals."""
from __future__ import annotations

from typing import Dict, List, Tuple

import pandas as pd

import config

# Status constants
ON_TIME = "on_time"
LATE = "late"
ABSENT = "absent"
OFF = "off"
EXCUSED = "excused"  # scheduled but on approved leave / comp-off
UNSCHEDULED = "unscheduled"

# Statuses that don't count toward the scheduled-shift denominator.
_NON_WORKING = {OFF, EXCUSED}

Interval = Tuple[pd.Timestamp, pd.Timestamp]


def _present_intervals_by_agent(intervals: pd.DataFrame) -> Dict[str, List[Interval]]:
    """Group present-state intervals by Zendesk agent."""
    out: Dict[str, List[Interval]] = {}
    if intervals.empty:
        return out
    present = intervals[intervals["present"]]
    for agent, grp in present.groupby("agent"):
        out[agent] = list(zip(grp["start_ts"], grp["end_ts"]))
    return out


def _overlap_minutes(intervals: List[Interval], start, end) -> float:
    """Total minutes of the intervals that fall inside [start, end]."""
    total = pd.Timedelta(0)
    for s, e in intervals:
        lo = max(s, start)
        hi = min(e, end)
        if hi > lo:
            total += hi - lo
    return total.total_seconds() / 60.0


def _first_present(intervals: List[Interval], start, end):
    """Earliest interval start among those overlapping [start, end]."""
    candidates = [s for s, e in intervals if e > start and s < end]
    return min(candidates) if candidates else None


def compute_attendance(
    roster_df: pd.DataFrame, intervals: pd.DataFrame
) -> pd.DataFrame:
    """Per-shift attendance.

    roster_df must include: agent, zendesk_agent, date, off, shift_start,
    shift_end. intervals must be the normalized Zendesk frame.
    """
    by_agent = _present_intervals_by_agent(intervals)
    grace = pd.Timedelta(minutes=config.GRACE_MIN)
    end_buffer = pd.Timedelta(minutes=config.SHIFT_END_BUFFER_MIN)
    rows = []

    for _, r in roster_df.iterrows():
        base = dict(
            agent=r["agent"],
            date=str(r["date"]),
            shift_start=r["shift_start"],
            shift_end=r["shift_end"],
            first_present_ts=None,
            late_minutes=None,
            covered_minutes=None,
            shift_minutes=None,
            expected_minutes=None,
            coverage_pct=None,
            under_hours=False,
            exception=None,
        )

        if r["off"]:
            exception = r.get("exception")
            rows.append({**base, "status": EXCUSED if exception else OFF,
                         "exception": exception})
            continue

        start, end = r["shift_start"], r["shift_end"]
        shift_minutes = (end - start).total_seconds() / 60.0
        # Agents may go offline early by the buffer; coverage is measured against
        # the effective window ending there, so leaving on time = full coverage.
        eff_end = max(start, end - end_buffer)
        expected_minutes = (eff_end - start).total_seconds() / 60.0
        agent_intervals = by_agent.get(r["zendesk_agent"], [])

        first = _first_present(agent_intervals, start, end)
        if first is None:
            rows.append(
                {**base, "status": ABSENT, "shift_minutes": round(shift_minutes, 1),
                 "expected_minutes": round(expected_minutes, 1),
                 "covered_minutes": 0.0, "coverage_pct": 0.0}
            )
            continue

        late_minutes = max(0.0, (first - start).total_seconds() / 60.0)
        status = ON_TIME if first <= start + grace else LATE
        covered = _overlap_minutes(agent_intervals, start, eff_end)
        coverage_pct = covered / expected_minutes if expected_minutes else 0.0

        rows.append({
            **base,
            "status": status,
            "first_present_ts": first,
            "late_minutes": round(late_minutes, 1),
            "covered_minutes": round(covered, 1),
            "shift_minutes": round(shift_minutes, 1),
            "expected_minutes": round(expected_minutes, 1),
            "coverage_pct": round(coverage_pct, 4),
            "under_hours": coverage_pct < config.UNDER_HOURS_THRESHOLD,
        })

    return pd.DataFrame(rows)


def summarize(att: pd.DataFrame) -> dict:
    """Headline KPIs over a computed frame (OFF and EXCUSED days excluded)."""
    worked = att[~att["status"].isin(_NON_WORKING)]
    scheduled = len(worked)
    if scheduled == 0:
        return dict(scheduled=0, attendance_pct=0.0, on_time_pct=0.0,
                    late=0, absent=0, avg_coverage_pct=0.0)
    present = worked[worked["status"].isin([ON_TIME, LATE])]
    on_time = (worked["status"] == ON_TIME).sum()
    late = (worked["status"] == LATE).sum()
    absent = (worked["status"] == ABSENT).sum()
    avg_cov = present["coverage_pct"].mean() if len(present) else 0.0
    return dict(
        scheduled=scheduled,
        attendance_pct=round(100.0 * len(present) / scheduled, 1),
        on_time_pct=round(100.0 * on_time / scheduled, 1),
        late=int(late),
        absent=int(absent),
        avg_coverage_pct=round(100.0 * (avg_cov or 0.0), 1),
    )
