"""Tests for the grid-based fairness roster scheduler."""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from attendance import scheduling  # noqa: E402


def _grid_current():
    """16 agents laid out exactly on the default 2/2, 3/3, 3/3 grid."""
    spec = (
        [("Morning", "Sun-Thurs")] * 2 + [("Morning", "Tues-Sat")] * 2 +
        [("Afternoon", "Sun-Thurs")] * 3 + [("Afternoon", "Tues-Sat")] * 3 +
        [("Night", "Sun-Thurs")] * 3 + [("Night", "Tues-Sat")] * 3
    )
    return pd.DataFrame([dict(agent=f"A{i}", shift=s, days=d)
                         for i, (s, d) in enumerate(spec)])


def test_shift_from_start():
    assert scheduling.shift_from_start(6) == "Morning"
    assert scheduling.shift_from_start(13) == "Afternoon"
    assert scheduling.shift_from_start(22) == "Night"


def test_current_allocation_excludes_fixed_agents():
    grid = pd.read_csv(config.SAMPLE_ROSTER_CSV, header=None, dtype=str,
                       keep_default_na=False)
    cur = scheduling.current_allocation(grid)
    assert "Piyush" not in set(cur["agent"]) and "Suhaib" not in set(cur["agent"])
    by = dict(zip(cur["agent"], cur["shift"]))
    assert by["Asjad"] == "Night" and by["Deepika"] == "Afternoon"


def test_recommend_meets_grid_and_includes_fixed():
    cur = _grid_current()
    rec = scheduling.recommend(cur, scheduling.load_history(), month="2026-07")
    assert rec["coverage"] == config.TARGET_GRID      # every (shift,day) cell met
    assert rec["unfilled_slots"] == 0 and rec["unassigned_agents"] == []
    fixed = rec["allocation"][rec["allocation"]["fixed"]]
    assert set(fixed["agent"]) == set(config.SCHEDULE_FIXED_AGENTS)
    assert len(rec["history_rows"].columns) == 4  # month, agent, shift, days


def test_recommend_is_deterministic():
    cur = _grid_current()
    r1 = scheduling.recommend(cur, scheduling.load_history(), month="2026-07")
    r2 = scheduling.recommend(cur, scheduling.load_history(), month="2026-07")
    assert r1["allocation"].equals(r2["allocation"])


def test_custom_targets_honoured():
    cur = pd.DataFrame([dict(agent=f"A{i}", shift="Night", days="Sun-Thurs")
                        for i in range(4)])
    targets = {"Morning": {"Sun-Thurs": 1, "Tues-Sat": 1},
               "Afternoon": {"Sun-Thurs": 1, "Tues-Sat": 0},
               "Night": {"Sun-Thurs": 1, "Tues-Sat": 0}}
    rec = scheduling.recommend(cur, scheduling.load_history(), month="2026-07",
                               targets=targets)
    assert rec["coverage"] == targets


def test_fairness_converges_over_months():
    cur = _grid_current()
    history = scheduling.load_history()
    last = None
    for m in range(1, 9):
        rec = scheduling.recommend(cur, history, month=f"2026-{m:02d}")
        history = pd.concat([history, rec["history_rows"]], ignore_index=True)
        a = rec["allocation"]
        cur = a[~a["fixed"]][["agent", "shift", "days"]].reset_index(drop=True)
        last = rec
    assert last["max_shift_spread"] <= 2      # bounded despite uneven 4/6/6
    assert last["avg_day_spread"] <= 1.0      # day-weeks are even (8/8)


def test_pool_smaller_than_grid_flags_unfilled():
    cur = pd.DataFrame([dict(agent=f"A{i}", shift="Night", days="Sun-Thurs")
                        for i in range(10)])  # fewer than 16
    rec = scheduling.recommend(cur, scheduling.load_history(), month="2026-07")
    assert rec["unfilled_slots"] == 6
