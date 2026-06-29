"""Support Team Attendance Dashboard (Streamlit).

Reconciles the Google Sheets roster against a Zendesk Explore agent-state
export and visualizes who was on time, late, or absent, and how much of each
shift was actually covered.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import config
from attendance import engine, identity, roster as roster_mod, scheduler, store, zendesk

st.set_page_config(page_title="Support Attendance", layout="wide")

STATUS_COLORS = {
    engine.ON_TIME: "#1b5e20",
    engine.LATE: "#b26a00",
    engine.ABSENT: "#8b0000",
    engine.OFF: "#444444",
    engine.EXCUSED: "#1565c0",
}
STATUS_LABEL = {
    engine.ON_TIME: "On time",
    engine.LATE: "Late",
    engine.ABSENT: "Absent",
    engine.OFF: "Off",
    engine.EXCUSED: "Leave / Comp-off",
}


def _init_state():
    for k in ("patterns", "roster_grid", "intervals", "intervals_all",
              "zendesk_agents", "raw_explore"):
        st.session_state.setdefault(k, None)


def load_patterns():
    """Read the roster grid once; parse the recurring patterns from it."""
    raw = roster_mod.read_roster_dataframe()
    st.session_state["roster_grid"] = raw
    patterns = roster_mod.parse_patterns(raw)
    st.session_state["patterns"] = patterns
    return patterns


# ---------------------------------------------------------------------------
# Sidebar — data sources & config
# ---------------------------------------------------------------------------
def sidebar():
    st.sidebar.header("Data sources")

    src = "Google Sheets" if config.GOOGLE_SHEETS_ID else "sample CSV (no Sheet configured)"
    st.sidebar.caption(f"Roster source: **{src}**")
    if st.sidebar.button("🔄 Load / refresh roster"):
        try:
            p = load_patterns()
            st.sidebar.success(f"Loaded {len(p)} agent shift patterns.")
        except Exception as exc:
            st.sidebar.error(str(exc))

    st.sidebar.divider()
    st.sidebar.subheader("Zendesk Explore export")
    uploaded = st.sidebar.file_uploader("Upload Explore CSV", type=["csv"])
    use_sample = st.sidebar.checkbox("Use sample export instead", value=False)

    source = None
    if uploaded is not None:
        source = uploaded
    elif use_sample and config.SAMPLE_EXPLORE_CSV.exists():
        source = str(config.SAMPLE_EXPLORE_CSV)

    if source is not None:
        raw = zendesk.load_explore_csv(source)
        st.session_state["raw_explore"] = raw
        guess = zendesk.suggest_mapping(raw)
        cols = list(raw.columns)
        none = "(none)"
        st.sidebar.caption("Map your export columns:")
        agent_c = st.sidebar.selectbox("Agent", cols, index=_idx(cols, guess.agent))
        status_c = st.sidebar.selectbox("Status", cols, index=_idx(cols, guess.status))
        date_c = st.sidebar.selectbox("Date column (if separate)", [none] + cols,
                                      index=_idx([none] + cols, guess.date))
        start_c = st.sidebar.selectbox("Start time", cols, index=_idx(cols, guess.start))
        end_mode = st.sidebar.radio("Interval end via", ["End time", "Duration"],
                                    index=0 if guess.end else 1, horizontal=True)
        end_c = dur_c = None
        if end_mode == "End time":
            end_c = st.sidebar.selectbox("End time", cols, index=_idx(cols, guess.end))
        else:
            dur_c = st.sidebar.selectbox("Duration", cols, index=_idx(cols, guess.duration))

        # Preferred channel — default to Unified (the agent's overall status),
        # with per-agent fallback to other channels when Unified is missing.
        chan_c = st.sidebar.selectbox("Channel column (optional)", [none] + cols,
                                      index=_idx([none] + cols, guess.channel))
        preferred_channel = None
        if chan_c != none:
            vals = sorted(v for v in raw[chan_c].astype(str).str.strip().unique() if v)
            opts = ["(none)"] + vals
            default = "Unified" if "Unified" in vals else "(none)"
            choice = st.sidebar.selectbox(
                "Preferred channel", opts, index=opts.index(default),
                help="Agents with no rows on this channel fall back to all channels.")
            preferred_channel = None if choice == "(none)" else choice

        tz_opts = ["Asia/Kolkata", "UTC", "America/New_York", "Europe/London"]
        export_tz = st.sidebar.selectbox(
            "Export timezone", tz_opts, index=_idx(tz_opts, config.EXPORT_TZ),
            help="Switch this if attendance times look shifted.")

        if st.sidebar.button("Apply mapping"):
            mapping = zendesk.ExploreMapping(
                agent=agent_c, status=status_c, start=start_c, end=end_c,
                duration=dur_c, date=None if date_c == none else date_c,
                channel=None if chan_c == none else chan_c)
            try:
                norm = zendesk.normalize_intervals(raw, mapping, export_tz)
                st.session_state["intervals_all"] = norm
                st.session_state["intervals"] = zendesk.merge_present_intervals(
                    norm, preferred_channel)
                st.session_state["zendesk_agents"] = sorted(norm["agent"].unique())
                st.sidebar.success(
                    f"Parsed {len(norm)} rows · {len(st.session_state['intervals'])} "
                    f"present intervals · {len(st.session_state['zendesk_agents'])} agents."
                )
            except Exception as exc:
                st.sidebar.error(str(exc))

    st.sidebar.divider()
    st.sidebar.subheader("Roster source")
    st.sidebar.radio(
        "Compute attendance against", ["Google Sheet", "Auto-generated (rotation)"],
        key="roster_source",
        help="Auto-generated uses the quarterly rotation from the seed placement.")

    st.sidebar.divider()
    st.sidebar.subheader("Rules")
    st.sidebar.caption(
        f"Timezone: **{config.APP_TZ}**  ·  Grace: **{config.GRACE_MIN} min**  ·  "
        f"Present: **{', '.join(config.PRESENT_STATES)}**"
    )
    st.sidebar.caption("Change these in config.py / .env, then refresh.")


def _idx(cols, value):
    return cols.index(value) if value in cols else 0


# ---------------------------------------------------------------------------
# Agent identity mapping
# ---------------------------------------------------------------------------
def identity_section(roster_df, zendesk_agents):
    roster_agents = sorted(roster_df["agent"].unique())
    zendesk_agents = sorted(zendesk_agents or [])
    saved = store.get_agent_map()
    seed = identity.default_map(roster_agents, zendesk_agents)
    for k, v in saved.items():
        if k in seed and v:
            seed[k] = v

    with st.expander("👥 Agent identity mapping (roster name → Zendesk agent)", expanded=False):
        editor_df = pd.DataFrame(
            {"roster_name": list(seed.keys()), "zendesk_agent": list(seed.values())}
        )
        edited = st.data_editor(
            editor_df,
            column_config={
                "zendesk_agent": st.column_config.SelectboxColumn(
                    "Zendesk agent", options=[""] + zendesk_agents
                )
            },
            hide_index=True,
            use_container_width=True,
            key="agent_map_editor",
        )
        if st.button("Save mapping"):
            store.set_agent_map(dict(zip(edited["roster_name"], edited["zendesk_agent"])))
            st.success("Mapping saved.")

    current = store.get_agent_map() or seed
    missing = identity.unmapped_agents(roster_agents, zendesk_agents, current)
    if missing and zendesk_agents:
        st.warning(
            "No Zendesk activity matched for: " + ", ".join(missing)
            + ". Map them above or they'll show as absent."
        )
    return current


# ---------------------------------------------------------------------------
# Dashboard rendering
# ---------------------------------------------------------------------------
def kpi_row(summary):
    c = st.columns(5)
    c[0].metric("Attendance", f"{summary['attendance_pct']}%")
    c[1].metric("On time", f"{summary['on_time_pct']}%")
    c[2].metric("Late", summary["late"])
    c[3].metric("Absent", summary["absent"])
    c[4].metric("Avg coverage", f"{summary['avg_coverage_pct']}%")


def roster_scheduler():
    """Show the auto-generated monthly rotation schedule + CSV download."""
    from datetime import date as _date
    try:
        seed = scheduler.load_seed()
    except Exception as exc:
        st.info("Roster scheduler unavailable: " + str(exc))
        return

    st.subheader("Monthly shift schedule")
    st.caption(
        "Every agent rotates one step each month (Night → Afternoon → Morning); "
        "the working-days pattern advances every 3 months, after a full cycle. "
        "Per-shift headcount rotates among the shifts since the team splits "
        "unevenly across three shifts.")

    # Monthly view: next 12 months from the current month.
    cur = max(0, scheduler.period_index(_date.today()))
    months = list(range(cur, cur + 12))
    table = scheduler.schedule_table(months, seed)
    st.dataframe(table, hide_index=True, use_container_width=True)

    this_cov = scheduler.coverage(scheduler.assign(seed, cur))
    st.caption("This month's coverage: "
               + "  ·  ".join(f"{k} {this_cov.get(k, 0)}" for k in config.SHIFT_CHRONOLOGY))

    st.download_button(
        "⬇️ Download schedule CSV", table.to_csv(index=False),
        file_name="roster_schedule.csv", mime="text/csv")


def generated_roster(dates, leaves):
    """Build a roster frame from the rotation, expanding per quarter over dates."""
    seed = scheduler.load_seed()
    by_q = {}
    for d in dates:
        by_q.setdefault(scheduler.quarter_index(d), []).append(d)
    parts = [roster_mod.expand(scheduler.generate_patterns(ds[0], seed), ds, leaves)
             for ds in by_q.values()]
    return pd.concat(parts, ignore_index=True)


def agent_summary(att):
    """Per-agent attendance rollup with in-row bars (matches the chat visual)."""
    backoffice = config.INVISIBLE_AS_PRESENT_AGENTS
    rows = []
    for ag in sorted(att["agent"].unique()):
        a = att[att["agent"] == ag]
        sched = int(a["status"].isin([engine.ON_TIME, engine.LATE, engine.ABSENT]).sum())
        on_time = int((a["status"] == engine.ON_TIME).sum())
        late = int((a["status"] == engine.LATE).sum())
        absent = int((a["status"] == engine.ABSENT).sum())
        excused = int((a["status"] == engine.EXCUSED).sum())
        present = on_time + late
        att_pct = round(100 * present / sched, 1) if sched else 0.0
        cov = a[a["status"].isin([engine.ON_TIME, engine.LATE])]["coverage_pct"].mean()
        cov_pct = round(100 * cov, 1) if pd.notna(cov) else 0.0
        if sched > 0 and present == 0 and absent == sched:
            note = "no Zendesk data"
        elif any(n in ag.lower() for n in backoffice):
            note = "via Invisible"
        else:
            note = ""
        rows.append({
            "Agent": ag, "Scheduled": sched, "On time": on_time, "Late": late,
            "Absent": absent, "Excused": excused, "Attendance %": att_pct,
            "Coverage %": cov_pct, "Note": note,
        })
    df = pd.DataFrame(rows).sort_values("Attendance %", ascending=False)

    st.subheader("Attendance by agent")
    st.dataframe(
        df, hide_index=True, use_container_width=True,
        column_config={
            "Attendance %": st.column_config.ProgressColumn(
                "Attendance %", format="%.0f%%", min_value=0, max_value=100),
            "Coverage %": st.column_config.ProgressColumn(
                "Coverage %", format="%.0f%%", min_value=0, max_value=100),
        },
    )


def status_grid(att):
    grid = att.pivot_table(index="agent", columns="date", values="status",
                           aggfunc="first")
    grid = grid.fillna("")

    def color(v):
        return f"background-color: {STATUS_COLORS.get(v, 'transparent')}; color: white"

    st.dataframe(grid.style.applymap(color), use_container_width=True)
    st.caption("🟩 On time   🟧 Late   🟥 Absent   🟦 Leave/Comp-off   ⬛ Off")


def daily_trend(att):
    worked = att[~att["status"].isin([engine.OFF, engine.EXCUSED])].copy()
    if worked.empty:
        return
    worked["present"] = worked["status"].isin([engine.ON_TIME, engine.LATE]).astype(int)
    by_day = worked.groupby("date").agg(
        attendance_pct=("present", lambda s: round(100 * s.mean(), 1)),
        avg_coverage=("coverage_pct", lambda s: round(100 * s.dropna().mean(), 1)
                      if s.notna().any() else 0.0),
    )
    st.subheader("Daily trend")
    st.bar_chart(by_day)


def drilldown(att):
    agents = sorted(att["agent"].unique())
    if not agents:
        return
    st.subheader("Agent drill-down")
    agent = st.selectbox("Agent", agents)
    sub = att[att["agent"] == agent].copy()
    show = sub[["date", "status", "shift_start", "shift_end", "first_present_ts",
                "late_minutes", "covered_minutes", "expected_minutes",
                "coverage_pct", "under_hours"]]
    show = show.assign(coverage_pct=(show["coverage_pct"] * 100).round(1))
    st.dataframe(show, hide_index=True, use_container_width=True)


def _autoload_export():
    """Auto-load + map a configured export so the dashboard populates on launch."""
    import os
    path = config.ZENDESK_EXPORT_PATH
    if not path or not os.path.exists(path) or st.session_state.get("intervals") is not None:
        return
    try:
        raw = zendesk.load_explore_csv(path)
        mapping = zendesk.suggest_mapping(raw)
        norm = zendesk.normalize_intervals(raw, mapping, config.EXPORT_TZ)
        pref = config.PREFERRED_CHANNEL if mapping.channel else None
        st.session_state["intervals_all"] = norm
        st.session_state["intervals"] = zendesk.merge_present_intervals(norm, pref)
        st.session_state["zendesk_agents"] = sorted(norm["agent"].unique())
    except Exception as exc:
        st.warning(f"Auto-load of export failed ({exc}); upload it manually.")


def attendance_view():
    """The Attendance tab: roster vs. actual Zendesk online time."""
    patterns = st.session_state.get("patterns")
    if patterns is None:
        try:
            patterns = load_patterns()
        except Exception as exc:
            st.info("Load a roster from the sidebar to begin. " + str(exc))
            return
    if patterns.empty:
        st.warning("No shift patterns parsed from the roster. Check the sheet layout.")
        return

    dupes = patterns["agent"].value_counts()
    dupes = dupes[dupes > 1]
    if len(dupes):
        detail = ", ".join(
            f"{a} ({'; '.join(patterns[patterns['agent'] == a]['pattern'])})"
            for a in dupes.index
        )
        st.warning(f"⚠️ Agent appears in multiple shift patterns in the roster: {detail}. "
                   "The roster may need cleanup; all listed shifts are evaluated.")

    intervals = st.session_state.get("intervals")
    norm_all = st.session_state.get("intervals_all")
    if intervals is None or norm_all is None:
        st.info("Upload a Zendesk Explore export (or tick 'Use sample export') "
                "and click **Apply mapping** in the sidebar.")
        return

    grid = st.session_state.get("roster_grid")
    leaves = roster_mod.parse_leaves(grid) if grid is not None else {}

    # The recurring roster is expanded over the date range the export covers.
    ex_dates = norm_all["start_ts"].dt.date
    dates = roster_mod.date_span(ex_dates.min(), ex_dates.max())
    if st.session_state.get("roster_source", "").startswith("Auto"):
        roster_df = generated_roster(dates, leaves)
    else:
        roster_df = roster_mod.expand(patterns, dates, leaves)

    agent_map = identity_section(roster_df, st.session_state.get("zendesk_agents"))

    roster_mapped = identity.attach_zendesk_names(roster_df, agent_map)
    att = engine.compute_attendance(roster_mapped, intervals)
    store.save_attendance(att)

    # Date filter
    dates = sorted(att["date"].unique())
    if dates:
        lo, hi = st.select_slider("Date range", options=dates,
                                  value=(dates[0], dates[-1]))
        att = att[(att["date"] >= lo) & (att["date"] <= hi)]

    kpi_row(engine.summarize(att))
    st.divider()
    agent_summary(att)
    status_grid(att)
    daily_trend(att)
    drilldown(att)


def main():
    _init_state()
    st.title("📞 Support Team Attendance & Roster")
    sidebar()
    _autoload_export()

    tab_att, tab_roster = st.tabs(["📊 Attendance", "📅 Roster scheduling"])
    with tab_att:
        attendance_view()
    with tab_roster:
        roster_scheduler()


if __name__ == "__main__":
    main()
