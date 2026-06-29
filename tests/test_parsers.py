"""Tests for roster and Zendesk export parsing against the sample files."""
import os
import sys
from datetime import date

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from attendance import roster, zendesk  # noqa: E402


# --- roster: pattern parsing -------------------------------------------------
def test_parse_day_range():
    assert roster.parse_day_range("Sun-Thurs") == [6, 0, 1, 2, 3]
    assert roster.parse_day_range("Tues-Sat") == [1, 2, 3, 4, 5]
    assert roster.parse_day_range("Mon-Fri") == [0, 1, 2, 3, 4]


def test_parse_timing_ampm_and_overnight():
    assert roster.parse_timing("6:30AM - 3:30PM") == (6, 30, 15, 30)
    assert roster.parse_timing("2PM - 11PM") == (14, 0, 23, 0)
    assert roster.parse_timing("10PM - 7AM") == (22, 0, 7, 0)
    assert roster.parse_timing("12AM - 12PM") == (0, 0, 12, 0)
    assert roster.parse_timing("Leave") is None


def test_parse_patterns_from_sample():
    grid = pd.read_csv(config.SAMPLE_ROSTER_CSV, header=None, dtype=str,
                       keep_default_na=False)
    pats = roster.parse_patterns(grid)
    agents = set(pats["agent"])
    # Top block only; the dummy bottom block (Shift/Name/Morning) is ignored.
    assert agents == {"Ashish", "Akshat", "Zaki", "Deepika", "Asjad", "Yash"}
    ashish = pats[pats["agent"] == "Ashish"].iloc[0]
    assert ashish["pattern"] == "Sun-Thurs"
    assert (ashish["start_h"], ashish["end_h"]) == (6, 15)


def test_expand_marks_off_days_and_overnight():
    grid = pd.read_csv(config.SAMPLE_ROSTER_CSV, header=None, dtype=str,
                       keep_default_na=False)
    pats = roster.parse_patterns(grid)
    monday = date(2026, 6, 22)  # a Monday
    exp = roster.expand(pats, [monday])

    # Zaki works Tues-Sat -> off on Monday.
    zaki = exp[exp["agent"] == "Zaki"].iloc[0]
    assert bool(zaki["off"]) is True
    # Ashish works Sun-Thurs -> on Monday, 06:30-15:30 same day.
    ashish = exp[exp["agent"] == "Ashish"].iloc[0]
    assert bool(ashish["off"]) is False
    assert ashish["shift_start"].hour == 6 and ashish["shift_end"].day == 22
    # Asjad 10PM-7AM -> overnight, ends next day.
    asjad = exp[exp["agent"] == "Asjad"].iloc[0]
    assert asjad["shift_start"].day == 22 and asjad["shift_end"].day == 23


# --- roster: leaves ----------------------------------------------------------
def test_parse_date_header():
    assert roster.parse_date_header("May 31") == (5, 31)
    assert roster.parse_date_header("Jun 1") == (6, 1)
    assert roster.parse_date_header("CS facing days") is None


def test_parse_leaves_from_sample():
    grid = pd.read_csv(config.SAMPLE_ROSTER_CSV, header=None, dtype=str,
                       keep_default_na=False)
    leaves = roster.parse_leaves(grid)
    # Bottom block: Ashish on Leave on May 31.
    assert leaves.get(("Ashish", 5, 31)) == "leave"


def test_expand_marks_leave_as_excused():
    grid = pd.read_csv(config.SAMPLE_ROSTER_CSV, header=None, dtype=str,
                       keep_default_na=False)
    pats = roster.parse_patterns(grid)
    leaves = roster.parse_leaves(grid)
    # May 31 2026 is a Sunday -> Ashish (Sun-Thurs) is scheduled, but on leave.
    exp = roster.expand(pats, [date(2026, 5, 31)], leaves)
    ashish = exp[exp["agent"] == "Ashish"].iloc[0]
    assert bool(ashish["off"]) is True
    assert ashish["exception"] == "leave"


# --- zendesk export parsing --------------------------------------------------
def test_zendesk_normalizes_sample():
    raw = zendesk.load_explore_csv(config.SAMPLE_EXPLORE_CSV)
    mapping = zendesk.suggest_mapping(raw)
    assert mapping.agent == "Agent"
    assert mapping.status == "Status"
    assert mapping.start and mapping.end
    norm = zendesk.normalize_intervals(raw, mapping)
    assert {"agent", "status", "present", "start_ts", "end_ts"}.issubset(norm.columns)
    # statuses are normalized to lowercase (and 'Unified ' prefix stripped)
    assert norm[norm["status"] == "online"]["present"].all()
    assert not norm[norm["status"] == "offline"]["present"].any()


def test_invisible_counts_present_only_for_backoffice():
    raw = pd.DataFrame({
        "Agent": ["Suhaib Khaleel", "Gilchrist"],
        "Status": ["Invisible", "Invisible"],
        "Start": ["2026-06-22 09:00:00+05:30", "2026-06-22 09:00:00+05:30"],
        "End": ["2026-06-22 17:00:00+05:30", "2026-06-22 17:00:00+05:30"],
    })
    mapping = zendesk.ExploreMapping(agent="Agent", status="Status",
                                     start="Start", end="End")
    norm = zendesk.normalize_intervals(raw, mapping)
    # Suhaib (back-office) -> Invisible present; Gilchrist (chat agent) -> not.
    assert bool(norm[norm["agent"] == "Suhaib Khaleel"]["present"].iloc[0]) is True
    assert bool(norm[norm["agent"] == "Gilchrist"]["present"].iloc[0]) is False


def test_duration_based_end():
    raw = pd.DataFrame({
        "Agent": ["X"], "Status": ["Online"],
        "Start": ["2026-06-22 09:00:00+05:30"], "Dur": ["01:30:00"],
    })
    mapping = zendesk.ExploreMapping(agent="Agent", status="Status",
                                     start="Start", duration="Dur")
    norm = zendesk.normalize_intervals(raw, mapping)
    delta = (norm.iloc[0]["end_ts"] - norm.iloc[0]["start_ts"]).total_seconds()
    assert delta == 90 * 60
