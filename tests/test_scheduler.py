"""Tests for the automated roster scheduler."""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from attendance import roster, scheduler  # noqa: E402


def seed():
    return scheduler.load_seed()


def test_period_index_anchor_and_advance():
    anchor = date(2026, 1, 1)
    # Monthly rotation: one period per month.
    assert scheduler.period_index(date(2026, 1, 1), anchor) == 0
    assert scheduler.period_index(date(2026, 1, 31), anchor) == 0
    assert scheduler.period_index(date(2026, 2, 1), anchor) == 1
    assert scheduler.period_index(date(2026, 4, 1), anchor) == 3
    assert scheduler.period_index(date(2027, 1, 1), anchor) == 12


def test_headcount_total_constant_and_coverage_rotates():
    s = seed()
    total = len(s)
    base = scheduler.coverage(scheduler.assign(s, 0))
    for p in range(6):
        cov = scheduler.coverage(scheduler.assign(s, p))
        assert sum(cov.values()) == total          # everyone always placed
        assert sorted(cov.values()) == sorted(base.values())  # counts just rotate


def test_timing_advances_one_step_each_month():
    s = seed()
    a0 = scheduler.assign(s, 0).set_index("agent")
    a1 = scheduler.assign(s, 1).set_index("agent")
    night = a0[a0["shift"] == "Night"].index[0]
    assert a1.loc[night, "shift"] == "Afternoon"


def test_every_agent_cycles_through_all_shifts_and_no_imperfect():
    s = seed()
    shifts_seen = {ag: set() for ag in s["agent"]}
    for p in range(3):  # one full timing cycle
        a = scheduler.assign(s, p)
        assert int(a["imperfect_step"].sum()) == 0
        for _, r in a.iterrows():
            shifts_seen[r["agent"]].add(r["shift"])
    for ag, seen in shifts_seen.items():
        assert seen == set(config.SHIFT_CHRONOLOGY), f"{ag} missed a shift: {seen}"


def test_day_pattern_holds_for_full_cycle_then_advances():
    s = seed()
    agent = "Akshat"
    p = [scheduler.assign(s, q).set_index("agent").loc[agent, "days"] for q in range(6)]
    # Same pattern across the first 3-quarter timing cycle, then advances.
    assert p[0] == p[1] == p[2]
    assert p[3] == p[4] == p[5]
    assert p[0] != p[3]


def test_abuse_analysts_fixed_and_present():
    pats = scheduler.generate_patterns(date(2026, 6, 15))
    for name in config.SCHEDULE_FIXED_AGENTS:
        row = pats[pats["agent"] == name]
        assert len(row) == 1
        assert row.iloc[0]["pattern"] == config.SCHEDULE_FIXED_PATTERN


def test_generate_patterns_plugs_into_expand():
    pats = scheduler.generate_patterns(date(2026, 1, 15))
    # Same schema as roster.parse_patterns -> roster.expand consumes it.
    exp = roster.expand(pats, [date(2026, 1, 5)])  # a Monday
    assert {"agent", "date", "off", "shift_start", "shift_end"}.issubset(exp.columns)
    assert len(exp) == len(pats)
