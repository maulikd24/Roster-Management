"""Reconcile roster display names with Zendesk agent identifiers."""
from __future__ import annotations

from typing import Dict, List

import pandas as pd


def resolve_zendesk_name(roster_name: str, agent_map: Dict[str, str]) -> str:
    """Translate a roster name to its Zendesk identifier (identity if unmapped)."""
    return agent_map.get(roster_name, roster_name)


def unmapped_agents(
    roster_agents: List[str],
    zendesk_agents: List[str],
    agent_map: Dict[str, str],
) -> List[str]:
    """Roster agents whose resolved Zendesk name isn't present in the export."""
    zset = set(zendesk_agents)
    missing = []
    for name in sorted(set(roster_agents)):
        resolved = resolve_zendesk_name(name, agent_map)
        if resolved not in zset:
            missing.append(name)
    return missing


def default_map(roster_agents: List[str], zendesk_agents: List[str]) -> Dict[str, str]:
    """Seed a mapping, matching roster names to Zendesk names.

    Tries, in order: exact (case-insensitive), Zendesk full name starting with
    the roster name (e.g. "Julia" -> "Julia Hanna Oommen"), and first-name match
    (e.g. "Akshay" -> "Akshay Kumar").
    """
    z_by_lower = {z.lower(): z for z in zendesk_agents}
    out = {}
    for name in sorted(set(roster_agents)):
        key = name.lower()
        match = z_by_lower.get(key)
        if not match:
            match = next((z for z in zendesk_agents
                          if z.lower().startswith(key + " ")), None)
        if not match:
            match = next((z for z in zendesk_agents
                          if z.lower().split() and z.lower().split()[0] == key.split()[0]),
                         None)
        if not match:  # roster name appears as any token (e.g. "Zaki" in "Mohd Zaki Haji")
            match = next((z for z in zendesk_agents if key in z.lower().split()), None)
        out[name] = match or ""
    return out


def attach_zendesk_names(
    roster_df: pd.DataFrame, agent_map: Dict[str, str]
) -> pd.DataFrame:
    df = roster_df.copy()
    df["zendesk_agent"] = df["agent"].map(
        lambda n: resolve_zendesk_name(n, agent_map)
    )
    return df
