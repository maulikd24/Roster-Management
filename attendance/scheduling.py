"""Fairness-based roster scheduler.

Assigns the next month's shifts by assessing the **full history** of previously
allocated shifts so each agent's lifetime mix across Night / Afternoon / Morning
stays as even as possible, while still meeting per-shift headcount targets.

History is a tidy frame `month, agent, shift` (kept in a Google Sheet tab or an
uploaded CSV). The current sheet allocation is folded in as the latest month, so
fairness works even before a history tab is populated.

Abuse analysts (config.SCHEDULE_FIXED_AGENTS) stay on fixed timings and are not
part of the rotating pool. Working-days patterns are carried forward from the
current allocation.
"""
from __future__ import annotations

import io
from datetime import date
from typing import Dict, List, Optional

import pandas as pd

import config
from attendance import roster

SHIFTS = config.SHIFT_CHRONOLOGY  # fill order: Night, Afternoon, Morning


def shift_from_start(hour: int) -> str:
    """Map a shift start hour to a shift name."""
    if hour < 12:
        return "Morning"
    if hour < 20:
        return "Afternoon"
    return "Night"


def _is_fixed(agent: str) -> bool:
    a = agent.lower()
    return any(k.lower() in a for k in config.SCHEDULE_FIXED_AGENTS)


def next_month(today: date = None) -> str:
    """'YYYY-MM' of the month after `today`."""
    today = today or date.today()
    y, m = today.year, today.month + 1
    if m > 12:
        y, m = y + 1, 1
    return f"{y:04d}-{m:02d}"


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------
def current_allocation(grid: pd.DataFrame) -> pd.DataFrame:
    """Parse the sheet's top block into agent -> (shift, days), excluding fixed."""
    pats = roster.parse_patterns(grid)
    rows = []
    for _, r in pats.iterrows():
        if _is_fixed(r["agent"]):
            continue
        rows.append(dict(agent=r["agent"], shift=shift_from_start(int(r["start_h"])),
                         days=r["pattern"]))
    return pd.DataFrame(rows).drop_duplicates("agent").reset_index(drop=True)


def load_history(data=None, url: str = None) -> pd.DataFrame:
    """Read shift history (month, agent, shift) from CSV bytes/text or a URL.

    Returns an empty frame (correct columns) when nothing is provided.
    """
    cols = ["month", "agent", "shift"]
    if url:
        df = pd.read_csv(roster.csv_export_url(url), dtype=str, keep_default_na=False)
    elif data is not None:
        buf = io.BytesIO(data) if isinstance(data, (bytes, bytearray)) else io.StringIO(data)
        df = pd.read_csv(buf, dtype=str, keep_default_na=False)
    else:
        return pd.DataFrame(columns=cols)
    df.columns = [c.strip().lower() for c in df.columns]
    for c in cols:
        if c not in df.columns:
            raise ValueError(f"History is missing a '{c}' column.")
    df = df[cols].copy()
    for c in cols:
        df[c] = df[c].astype(str).str.strip()
    return df


# ---------------------------------------------------------------------------
# Fairness assignment
# ---------------------------------------------------------------------------
def _counts(history: pd.DataFrame, pool: List[str]) -> Dict[str, Dict[str, int]]:
    counts = {a: {s: 0 for s in SHIFTS} for a in pool}
    for _, r in history.iterrows():
        if r["agent"] in counts and r["shift"] in counts[r["agent"]]:
            counts[r["agent"]][r["shift"]] += 1
    return counts


def recommend(current: pd.DataFrame, history: pd.DataFrame,
              month: str = None, targets: Optional[Dict[str, int]] = None) -> dict:
    """Recommend the next month's allocation balancing lifetime shift mix."""
    month = month or next_month()
    pool = list(current["agent"])

    counts = _counts(history, pool)
    # Bootstrap only: if there's no history yet, count the current allocation so
    # the first recommendation still reflects what people just worked. Once a
    # history tab exists (it should include the current month), we use it alone.
    if history.empty:
        for _, r in current.iterrows():
            if r["shift"] in counts.get(r["agent"], {}):
                counts[r["agent"]][r["shift"]] += 1

    if targets is None:
        targets = current["shift"].value_counts().to_dict()
    targets = {s: int(targets.get(s, 0)) for s in SHIFTS}

    # Capacity-preserving slot assignment that minimizes "shift repeats" (i.e.
    # balances each agent's lifetime mix). Start from a deterministic filling,
    # then 2-opt swap between shifts while it lowers total historical cost.
    slots: List[str] = []
    for s in SHIFTS:
        slots += [s] * targets[s]
    while len(slots) < len(pool):
        slots.append(SHIFTS[-1])
    slots = slots[:len(pool)]

    agents = sorted(pool)
    assign: Dict[str, str] = {a: slots[i] for i, a in enumerate(agents)}
    improved = True
    while improved:
        improved = False
        for i in range(len(agents)):
            for j in range(i + 1, len(agents)):
                a, b = agents[i], agents[j]
                sa, sb = assign[a], assign[b]
                if sa == sb:
                    continue
                if counts[a][sb] + counts[b][sa] < counts[a][sa] + counts[b][sb]:
                    assign[a], assign[b] = sb, sa
                    improved = True

    days_map = dict(zip(current["agent"], current["days"]))
    rows = [dict(agent=a, shift=assign[a], days=days_map.get(a, ""), fixed=False)
            for a in pool]

    fixed_rows = [dict(agent=name, shift=shift_from_start(t[0]),
                       days=config.SCHEDULE_FIXED_PATTERN, fixed=True)
                  for name, t in config.SCHEDULE_FIXED_AGENTS.items()]

    allocation = pd.concat([pd.DataFrame(rows), pd.DataFrame(fixed_rows)],
                           ignore_index=True)
    history_rows = pd.DataFrame(
        [dict(month=month, agent=a, shift=assign[a]) for a in pool])

    # Fairness metric: per-agent spread (max-min across shifts) after this month.
    after = {a: dict(counts[a]) for a in pool}
    for a in pool:
        after[a][assign[a]] += 1
    spreads = [max(c.values()) - min(c.values()) for c in after.values()]

    return dict(
        month=month,
        allocation=allocation,
        coverage=allocation[~allocation["fixed"]]["shift"].value_counts().to_dict(),
        history_rows=history_rows,
        avg_spread=round(sum(spreads) / len(spreads), 2) if spreads else 0.0,
        max_spread=max(spreads) if spreads else 0,
    )
