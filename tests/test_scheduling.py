"""Tests for the fairness-based roster scheduler."""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from attendance import scheduling  # noqa: E402


def _current(spec):
    return pd.DataFrame([dict(agent=a, shift=s, days="Sun-Thurs") for a, s in spec])


def test_shift_from_start():
    assert scheduling.shift_from_start(6) == "Morning"
    assert scheduling.shift_from_start(13) == "Afternoon"
    assert scheduling.shift_from_start(22) == "Night"


def test_current_allocation_excludes_fixed_agents():
    grid = pd.read_csv(config.SAMPLE_ROSTER_CSV, header=None, dtype=str,
                       keep_default_na=False)
    cur = scheduling.current_allocation(grid)
    agents = set(cur["agent"])
    assert "Piyush" not in agents and "Suhaib" not in agents
    by = dict(zip(cur["agent"], cur["shift"]))
    assert by["Asjad"] == "Night"
    assert by["Deepika"] == "Afternoon"


def test_recommend_respects_capacities():
    cur = _current([(f"A{i}", s) for i, s in enumerate(
        ["Night", "Night", "Night", "Afternoon", "Afternoon", "Afternoon",
         "Morning", "Morning", "Morning"])])
    rec = scheduling.recommend(cur, scheduling.load_history(), month="2026-07")
    assert rec["coverage"] == {"Night": 3, "Afternoon": 3, "Morning": 3}
    rotating = rec["allocation"][~rec["allocation"]["fixed"]]
    assert len(rotating) == 9
    assert set(rec["history_rows"]["agent"]) == set(cur["agent"])


def test_recommend_is_deterministic():
    cur = _current([("X", "Night"), ("Y", "Afternoon"), ("Z", "Morning")])
    r1 = scheduling.recommend(cur, scheduling.load_history(), month="2026-07")
    r2 = scheduling.recommend(cur, scheduling.load_history(), month="2026-07")
    assert r1["allocation"].equals(r2["allocation"])


def test_fairness_converges_over_months():
    cur = _current([(f"A{i}", s) for i, s in enumerate(
        ["Night", "Night", "Night", "Afternoon", "Afternoon", "Afternoon",
         "Morning", "Morning", "Morning"])])
    history = scheduling.load_history()
    last = None
    for m in range(1, 7):
        rec = scheduling.recommend(cur, history, month=f"2026-{m:02d}")
        history = pd.concat([history, rec["history_rows"]], ignore_index=True)
        alloc = rec["allocation"]
        cur = alloc[~alloc["fixed"]][["agent", "shift", "days"]].reset_index(drop=True)
        last = rec
    # Even pool (3/3/3) should balance to a near-perfect spread.
    assert last["max_spread"] <= 1


def test_fixed_agents_included():
    cur = _current([("X", "Night"), ("Y", "Afternoon"), ("Z", "Morning")])
    rec = scheduling.recommend(cur, scheduling.load_history(), month="2026-07")
    fixed = rec["allocation"][rec["allocation"]["fixed"]]
    assert set(fixed["agent"]) == set(config.SCHEDULE_FIXED_AGENTS)
