"""Fairness-based roster scheduler over a (shift x day-week) grid.

Assigns next month's roster so each agent's lifetime mix is balanced on BOTH
dimensions — shift (Night/Afternoon/Morning) and day-week (Sun-Thurs/Tues-Sat) —
while meeting a target headcount grid (config.TARGET_GRID, overridable).

History is `month, agent, shift, days` (Google Sheet tab or uploaded CSV); the
3-column form `month, agent, shift` is tolerated (day-week balancing skipped for
those rows). The current sheet allocation is folded in only to bootstrap when no
history exists yet.

Abuse analysts (config.SCHEDULE_FIXED_AGENTS) stay on fixed timings, outside the
rotating pool.
"""
from __future__ import annotations

import io
from datetime import date
from typing import Dict, List, Optional

import pandas as pd

import config
from attendance import roster

SHIFTS = config.SHIFT_CHRONOLOGY            # Night, Afternoon, Morning
DAYWEEKS = config.SCHEDULE_DAY_PATTERNS     # Sun-Thurs, Tues-Sat


def shift_from_start(hour: int) -> str:
    if hour < 12:
        return "Morning"
    if hour < 20:
        return "Afternoon"
    return "Night"


def _is_fixed(agent: str) -> bool:
    a = agent.lower()
    return any(k.lower() in a for k in config.SCHEDULE_FIXED_AGENTS)


def next_month(today: date = None) -> str:
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
    """Read shift history (month, agent, shift[, days]) from CSV bytes/text/URL."""
    if url:
        df = pd.read_csv(roster.csv_export_url(url), dtype=str, keep_default_na=False)
    elif data is not None:
        buf = io.BytesIO(data) if isinstance(data, (bytes, bytearray)) else io.StringIO(data)
        df = pd.read_csv(buf, dtype=str, keep_default_na=False)
    else:
        return pd.DataFrame(columns=["month", "agent", "shift", "days"])
    df.columns = [c.strip().lower() for c in df.columns]
    for c in ("month", "agent", "shift"):
        if c not in df.columns:
            raise ValueError(f"History is missing a '{c}' column.")
    if "days" not in df.columns:
        df["days"] = ""
    df = df[["month", "agent", "shift", "days"]].copy()
    for c in df.columns:
        df[c] = df[c].astype(str).str.strip()
    return df


# ---------------------------------------------------------------------------
# Fairness assignment over (shift, day-week) slots
# ---------------------------------------------------------------------------
def _grid_slots(targets: Dict[str, Dict[str, int]]) -> List[tuple]:
    slots = []
    for s in SHIFTS:
        for d in DAYWEEKS:
            slots += [(s, d)] * int(targets.get(s, {}).get(d, 0))
    return slots


def _counts(history: pd.DataFrame, pool: List[str]):
    sh = {a: {s: 0 for s in SHIFTS} for a in pool}
    dw = {a: {d: 0 for d in DAYWEEKS} for a in pool}
    for _, r in history.iterrows():
        a = r["agent"]
        if a in sh and r["shift"] in sh[a]:
            sh[a][r["shift"]] += 1
        if a in dw and r.get("days") in dw[a]:
            dw[a][r["days"]] += 1
    return sh, dw


def recommend(current: pd.DataFrame, history: pd.DataFrame,
              month: str = None, targets: Optional[dict] = None) -> dict:
    """Recommend next month's grid allocation, balancing shift + day-week."""
    month = month or next_month()
    targets = targets or config.TARGET_GRID
    pool = list(current["agent"])

    sh, dw = _counts(history, pool)
    if history.empty:  # bootstrap from the current allocation only
        for _, r in current.iterrows():
            if r["shift"] in sh.get(r["agent"], {}):
                sh[r["agent"]][r["shift"]] += 1
            if r["days"] in dw.get(r["agent"], {}):
                dw[r["agent"]][r["days"]] += 1

    slots = _grid_slots(targets)
    agents = sorted(pool)
    n = min(len(agents), len(slots))
    # Shift weighted above day-week (shift fairness is primary).
    cost = lambda a, sl: 4 * sh[a][sl[0]] + dw[a][sl[1]]

    # Greedy fill, scarcest shift first, so rationed shifts (e.g. Morning) go to
    # whoever has done them least; then 2-opt swaps polish the combined cost.
    shift_cap = {s: sum(targets.get(s, {}).get(d, 0) for d in DAYWEEKS) for s in SHIFTS}
    order = sorted(slots, key=lambda sl: (shift_cap[sl[0]], SHIFTS.index(sl[0]),
                                          DAYWEEKS.index(sl[1])))[:n]
    unassigned = set(agents)
    assigned = {}
    for sl in order:
        a = min(unassigned, key=lambda a: (sh[a][sl[0]], dw[a][sl[1]],
                                           sum(sh[a].values()), a))
        assigned[a] = sl
        unassigned.discard(a)

    keys = [a for a in agents if a in assigned]
    improved = True
    while improved:
        improved = False
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                a, b = keys[i], keys[j]
                sa, sb = assigned[a], assigned[b]
                if sa == sb:
                    continue
                if cost(a, sb) + cost(b, sa) < cost(a, sa) + cost(b, sb):
                    assigned[a], assigned[b] = sb, sa
                    improved = True

    rows = [dict(agent=a, shift=assigned[a][0], days=assigned[a][1], fixed=False)
            for a in keys]
    fixed_rows = [dict(agent=name, shift=shift_from_start(t[0]),
                       days=config.SCHEDULE_FIXED_PATTERN, fixed=True)
                  for name, t in config.SCHEDULE_FIXED_AGENTS.items()]
    allocation = pd.concat([pd.DataFrame(rows), pd.DataFrame(fixed_rows)],
                           ignore_index=True)

    history_rows = pd.DataFrame(
        [dict(month=month, agent=a, shift=assigned[a][0], days=assigned[a][1])
         for a in keys])

    # Fairness metrics after this month.
    after_sh = {a: dict(sh[a]) for a in keys}
    after_dw = {a: dict(dw[a]) for a in keys}
    for a in keys:
        after_sh[a][assigned[a][0]] += 1
        after_dw[a][assigned[a][1]] += 1
    shift_spread = [max(c.values()) - min(c.values()) for c in after_sh.values()]
    day_spread = [max(c.values()) - min(c.values()) for c in after_dw.values()]

    cov = {}
    for s in SHIFTS:
        cov[s] = {d: int(((allocation["shift"] == s) & (allocation["days"] == d)
                          & (~allocation["fixed"])).sum()) for d in DAYWEEKS}

    return dict(
        month=month,
        allocation=allocation,
        coverage=cov,
        history_rows=history_rows,
        unfilled_slots=len(slots) - n,
        unassigned_agents=sorted(unassigned),
        avg_shift_spread=round(sum(shift_spread) / len(shift_spread), 2) if shift_spread else 0.0,
        max_shift_spread=max(shift_spread) if shift_spread else 0,
        avg_day_spread=round(sum(day_spread) / len(day_spread), 2) if day_spread else 0.0,
    )
