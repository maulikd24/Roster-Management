"""Unit tests for the reconciliation engine."""
import os
import sys
from datetime import date

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from attendance import engine  # noqa: E402

TZ = "Asia/Kolkata"


def ts(s):
    return pd.Timestamp(s, tz=TZ)


def make_roster(rows):
    return pd.DataFrame(rows)


def shift(agent, start, end, off=False, exception=None):
    return dict(
        agent=agent,
        zendesk_agent=agent,
        date=date(2026, 6, 22),
        off=off,
        exception=exception,
        shift_start=None if off else ts(start),
        shift_end=None if off else ts(end),
    )


def make_intervals(rows):
    df = pd.DataFrame(rows, columns=["agent", "status", "start_ts", "end_ts"])
    df["present"] = df["status"].str.lower().isin(["online", "away"])
    return df


def interval(agent, status, start, end):
    return dict(agent=agent, status=status, start_ts=ts(start), end_ts=ts(end))


def test_on_time_within_grace():
    roster = make_roster([shift("Asha", "2026-06-22 09:00", "2026-06-22 18:00")])
    intervals = make_intervals([
        interval("Asha", "Online", "2026-06-22 09:03", "2026-06-22 18:05"),
    ])
    out = engine.compute_attendance(roster, intervals)
    row = out.iloc[0]
    assert row["status"] == engine.ON_TIME
    assert row["late_minutes"] == 3.0


def test_late_beyond_grace():
    roster = make_roster([shift("Bilal", "2026-06-22 12:00", "2026-06-22 21:00")])
    intervals = make_intervals([
        interval("Bilal", "Online", "2026-06-22 12:20", "2026-06-22 21:02"),
    ])
    row = engine.compute_attendance(roster, intervals).iloc[0]
    assert row["status"] == engine.LATE
    assert row["late_minutes"] == 20.0


def test_absent_when_no_presence():
    roster = make_roster([shift("Dana", "2026-06-22 09:00", "2026-06-22 18:00")])
    out = engine.compute_attendance(roster, make_intervals([]))
    assert out.iloc[0]["status"] == engine.ABSENT
    assert out.iloc[0]["coverage_pct"] == 0.0


def test_away_counts_as_present():
    roster = make_roster([shift("Asha", "2026-06-22 09:00", "2026-06-22 18:00")])
    intervals = make_intervals([
        interval("Asha", "Away", "2026-06-22 09:05", "2026-06-22 18:00"),
    ])
    row = engine.compute_attendance(roster, intervals).iloc[0]
    assert row["status"] == engine.ON_TIME


def test_offline_does_not_count():
    roster = make_roster([shift("Asha", "2026-06-22 09:00", "2026-06-22 18:00")])
    intervals = make_intervals([
        interval("Asha", "Offline", "2026-06-22 09:00", "2026-06-22 18:00"),
    ])
    assert engine.compute_attendance(roster, intervals).iloc[0]["status"] == engine.ABSENT


def test_overnight_shift_and_under_hours():
    # Shift 22:00 -> 06:00 next day; online only first ~4h => under hours.
    roster = make_roster([
        dict(agent="Chen", zendesk_agent="Chen", date=date(2026, 6, 22), off=False,
             shift_start=ts("2026-06-22 22:00"), shift_end=ts("2026-06-23 06:00")),
    ])
    intervals = make_intervals([
        interval("Chen", "Online", "2026-06-22 22:05", "2026-06-23 02:00"),
    ])
    row = engine.compute_attendance(roster, intervals).iloc[0]
    assert row["status"] == engine.ON_TIME
    assert row["under_hours"] is True or bool(row["under_hours"]) is True
    assert row["coverage_pct"] < 0.85


def test_coverage_clipped_to_shift_window():
    # Online well before and after the shift; coverage should cap at 100%.
    roster = make_roster([shift("Asha", "2026-06-22 09:00", "2026-06-22 18:00")])
    intervals = make_intervals([
        interval("Asha", "Online", "2026-06-22 06:00", "2026-06-22 22:00"),
    ])
    row = engine.compute_attendance(roster, intervals).iloc[0]
    assert row["coverage_pct"] == pytest.approx(1.0)
    assert row["late_minutes"] == 0.0


def test_leaving_at_end_buffer_is_full_coverage():
    # Shift 09:00-18:00; leaving 15 min early (17:45) should still be 100%.
    roster = make_roster([shift("Asha", "2026-06-22 09:00", "2026-06-22 18:00")])
    intervals = make_intervals([
        interval("Asha", "Online", "2026-06-22 09:00", "2026-06-22 17:45"),
    ])
    row = engine.compute_attendance(roster, intervals).iloc[0]
    assert row["expected_minutes"] == pytest.approx(525.0)  # 540 - 15
    assert row["coverage_pct"] == pytest.approx(1.0)
    assert bool(row["under_hours"]) is False


def test_leave_is_excused_not_absent():
    roster = make_roster([shift("Asha", None, None, off=True, exception="leave")])
    out = engine.compute_attendance(roster, make_intervals([]))
    row = out.iloc[0]
    assert row["status"] == engine.EXCUSED
    # Excused days are excluded from the scheduled denominator.
    summary = engine.summarize(out)
    assert summary["scheduled"] == 0
    assert summary["absent"] == 0


def test_off_day_skipped_in_summary():
    roster = make_roster([
        shift("Asha", None, None, off=True),
        shift("Bilal", "2026-06-22 12:00", "2026-06-22 21:00"),
    ])
    intervals = make_intervals([
        interval("Bilal", "Online", "2026-06-22 12:00", "2026-06-22 21:00"),
    ])
    out = engine.compute_attendance(roster, intervals)
    summary = engine.summarize(out)
    assert summary["scheduled"] == 1
    assert summary["attendance_pct"] == 100.0
