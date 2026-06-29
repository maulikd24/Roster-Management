"""SQLite persistence for computed attendance and the agent identity map."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Dict, Optional

import pandas as pd

import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS attendance (
    agent            TEXT NOT NULL,
    date             TEXT NOT NULL,
    shift_start      TEXT,
    shift_end        TEXT,
    first_present_ts TEXT,
    status           TEXT,
    late_minutes     REAL,
    covered_minutes  REAL,
    shift_minutes    REAL,
    expected_minutes REAL,
    coverage_pct     REAL,
    PRIMARY KEY (agent, date)
);

CREATE TABLE IF NOT EXISTS agent_map (
    roster_name   TEXT PRIMARY KEY,
    zendesk_agent TEXT
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(config.DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(_SCHEMA)


# --- Agent identity map ------------------------------------------------------
def get_agent_map() -> Dict[str, str]:
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT roster_name, zendesk_agent FROM agent_map"
        ).fetchall()
    return {r[0]: r[1] for r in rows if r[1]}


def set_agent_map(mapping: Dict[str, str]) -> None:
    init_db()
    with get_conn() as conn:
        conn.execute("DELETE FROM agent_map")
        conn.executemany(
            "INSERT INTO agent_map (roster_name, zendesk_agent) VALUES (?, ?)",
            [(k, v) for k, v in mapping.items()],
        )


# --- Attendance --------------------------------------------------------------
def save_attendance(df: pd.DataFrame) -> None:
    """Upsert computed attendance rows (idempotent on agent+date)."""
    init_db()
    if df.empty:
        return
    cols = [
        "agent", "date", "shift_start", "shift_end", "first_present_ts",
        "status", "late_minutes", "covered_minutes", "shift_minutes",
        "expected_minutes", "coverage_pct",
    ]
    records = [tuple(_to_str(row[c]) for c in cols) for _, row in df.iterrows()]
    placeholders = ",".join(["?"] * len(cols))
    with get_conn() as conn:
        conn.executemany(
            f"INSERT OR REPLACE INTO attendance ({','.join(cols)}) "
            f"VALUES ({placeholders})",
            records,
        )


def load_attendance(
    start: Optional[str] = None, end: Optional[str] = None
) -> pd.DataFrame:
    init_db()
    query = "SELECT * FROM attendance"
    params = []
    clauses = []
    if start:
        clauses.append("date >= ?")
        params.append(start)
    if end:
        clauses.append("date <= ?")
        params.append(end)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY date, agent"
    with get_conn() as conn:
        return pd.read_sql_query(query, conn, params=params)


def _to_str(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
