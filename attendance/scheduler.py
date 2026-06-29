"""Automated roster scheduler.

Generates the roster by rotating agents through shift timings every quarter,
starting from a user-provided seed placement (Jan 2026). Rules:

- Timing rotates each quarter in chronology Night -> Afternoon -> Morning.
- Coverage per shift is fixed every quarter (config.SHIFT_COUNTS, e.g. 6/6/4).
- The working-days pattern advances only after a full timing cycle (every 3
  quarters), so it changes ~every 9 months.
- Abuse analysts (config.SCHEDULE_FIXED_AGENTS) are excluded from rotation and
  kept on fixed Mon-Fri timings.

Because 6/6/4 is uneven, a clean Night->Afternoon->Morning step is impossible for
everyone: when the 6 Afternoon agents rotate, only 4 fit Morning, so 2 wrap to
Night and skip Morning that quarter. Coverage stays exact; those agents are
flagged via `imperfect_step`.

`generate_patterns(date)` emits the same schema as `roster.parse_patterns`
(agent, pattern, days, start_h, start_m, end_h, end_m) so the existing
`roster.expand()` + engine pipeline works unchanged.
"""
from __future__ import annotations

from datetime import date
from typing import List

import pandas as pd

import config
from attendance import roster


# ---------------------------------------------------------------------------
# Quarter math
# ---------------------------------------------------------------------------
def _anchor() -> date:
    return pd.to_datetime(config.SCHEDULE_ANCHOR).date()


def period_index(d: date, anchor: date = None) -> int:
    """Whole rotation periods between `anchor` and `d` (can be negative)."""
    anchor = anchor or _anchor()
    months = (d.year - anchor.year) * 12 + (d.month - anchor.month)
    if d.day < anchor.day:
        months -= 1
    return months // config.SCHEDULE_PERIOD_MONTHS


def period_start(p: int, anchor: date = None) -> date:
    anchor = anchor or _anchor()
    total = anchor.month - 1 + p * config.SCHEDULE_PERIOD_MONTHS
    year = anchor.year + total // 12
    month = total % 12 + 1
    return date(year, month, anchor.day)


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------
def load_seed() -> pd.DataFrame:
    """Read the seed placement (agent, shift, days)."""
    if not config.SCHEDULE_SEED_CSV.exists():
        raise RuntimeError(f"Seed file not found: {config.SCHEDULE_SEED_CSV}")
    df = pd.read_csv(config.SCHEDULE_SEED_CSV, dtype=str, keep_default_na=False)
    df["agent"] = df["agent"].str.strip()
    df["shift"] = df["shift"].str.strip()
    df["days"] = df["days"].str.strip()
    return df


# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------
def assign(seed: pd.DataFrame, p: int) -> pd.DataFrame:
    """Compute the shift/days assignment for period (month) index `p`.

    Every agent advances one chronology step per period
    (Night → Afternoon → Morning → Night), so everyone cycles fairly through all
    three timings. The working-days pattern advances after a full timing cycle
    (every len(chronology) periods = 3 months). Returns columns: agent, shift,
    days, imperfect_step (always False — this rotation is exact).
    """
    chrono = config.SHIFT_CHRONOLOGY
    patterns = config.SCHEDULE_DAY_PATTERNS
    nc, npat = len(chrono), len(patterns)

    rows = []
    for _, r in seed.iterrows():
        ci = chrono.index(r["shift"]) if r["shift"] in chrono else 0
        pi = patterns.index(r["days"]) if r["days"] in patterns else 0
        rows.append(dict(
            agent=r["agent"],
            shift=chrono[(ci + p) % nc],
            days=patterns[(pi + p // nc) % npat],
            imperfect_step=False,
        ))
    return pd.DataFrame(rows)


def _timing_patterns(assignment: pd.DataFrame) -> pd.DataFrame:
    """Turn a shift/days assignment into roster-style patterns."""
    rows = []
    for _, r in assignment.iterrows():
        sh, sm, eh, em = config.SHIFT_TIMINGS[r["shift"]]
        rows.append(dict(agent=r["agent"], pattern=r["days"],
                         days=roster.parse_day_range(r["days"]),
                         start_h=sh, start_m=sm, end_h=eh, end_m=em))
    return pd.DataFrame(rows)


def _fixed_patterns() -> pd.DataFrame:
    """Abuse analysts on their fixed timings (not rotated)."""
    rows = []
    pat = config.SCHEDULE_FIXED_PATTERN
    for agent, (sh, sm, eh, em) in config.SCHEDULE_FIXED_AGENTS.items():
        rows.append(dict(agent=agent, pattern=pat,
                         days=roster.parse_day_range(pat),
                         start_h=sh, start_m=sm, end_h=eh, end_m=em))
    return pd.DataFrame(rows)


def generate_patterns(d: date, seed: pd.DataFrame = None) -> pd.DataFrame:
    """Roster patterns (roster.parse_patterns schema) for the period of `d`."""
    seed = seed if seed is not None else load_seed()
    p = max(0, period_index(d))
    rotated = _timing_patterns(assign(seed, p))
    return pd.concat([rotated, _fixed_patterns()], ignore_index=True)


def schedule_table(periods: List[int], seed: pd.DataFrame = None) -> pd.DataFrame:
    """Wide per-agent view across periods (months) for the dashboard/CSV.

    Columns: Agent, then one '<shift> / <days>' column per month.
    """
    seed = seed if seed is not None else load_seed()
    base = assign(seed, periods[0])[["agent"]].copy()
    for p in periods:
        a = assign(seed, p).set_index("agent")
        col = f"{period_start(p):%b %Y}"
        base[col] = base["agent"].map(
            lambda ag, a=a: f"{a.loc[ag, 'shift']} / {a.loc[ag, 'days']}")
    return base.rename(columns={"agent": "Agent"})


def coverage(assignment: pd.DataFrame) -> dict:
    return assignment["shift"].value_counts().to_dict()
