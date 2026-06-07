"""TRER real-time visualizer — Streamlit entry point."""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from trerviz.dashboard import (
    render_all_summary,
    render_events,
    render_field_view,
    render_json_viewer,
    render_metric_cards,
    render_phase_banner,
    render_time_series,
    update_history,
)
from trerviz.state_client import (
    fetch_all_packets,
    fetch_plugin_packet,
    load_config,
    post_crow_control,
)

st.set_page_config(
    page_title="TRER Viz",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="expanded",
)

PLUGIN_OPTIONS = ["all", "crow", "infrastructure", "market"]


def _init_state(cfg: dict) -> None:
    if "history" not in st.session_state:
        st.session_state.history = {}
    if "config" not in st.session_state:
        st.session_state.config = cfg
    if "last_fetch" not in st.session_state:
        st.session_state.last_fetch = 0.0


def main() -> None:
    st.title("TRER Real-Time Visualizer")
    st.caption(
        "Adapter-based metrics layer — polls plugin state without modifying the core engine."
    )

    cfg = load_config()
    _init_state(cfg)

    with st.sidebar:
        st.header("Controls")
        plugin = st.selectbox("Plugin", PLUGIN_OPTIONS, index=0)
        auto_refresh = st.toggle("Auto refresh", value=True)
        poll_interval = st.slider(
            "Poll interval (s)",
            min_value=0.5,
            max_value=5.0,
            value=float(cfg.get("poll_interval_seconds", 1.0)),
            step=0.5,
        )
        force_demo = st.toggle("Force demo mode", value=False)
        if st.button("Refresh now"):
            st.session_state.last_fetch = 0.0
        st.divider()
        st.markdown("**Data priority**")
        st.markdown("1. HTTP `/api/state` (tiny-router style)")
        st.markdown("2. `trerviz/snapshots/*.json`")
        st.markdown("3. Demo packets")
        st.caption("gepa-viz: drop normalized snapshots in `trerviz/snapshots/`.")

        if plugin == "crow":
            st.divider()
            st.subheader("Crow flock")
            start_count = st.slider("Start count", 1, 12, 3, key="crow_start_count")
            formation = st.selectbox(
                "Formation",
                ["patrol_wedge", "roost_cluster", "scout_line"],
                key="crow_formation",
            )
            preset = st.selectbox(
                "Role mix",
                ["balanced", "patrol_heavy", "scout_heavy", "juvenile_heavy"],
                key="crow_preset",
            )
            if st.button("Reset flock", key="crow_reset"):
                ok, detail = post_crow_control(
                    {
                        "action": "reset_flock",
                        "count": start_count,
                        "formation": formation,
                        "preset": preset,
                    },
                    cfg,
                )
                if ok:
                    st.success("Flock reset")
                else:
                    st.error(detail)
            if st.button("+ Spawn crow", key="crow_spawn"):
                ok, detail = post_crow_control(
                    {"action": "spawn_crow", "sex": "unknown"},
                    cfg,
                )
                if ok:
                    st.success("Crow spawned")
                else:
                    st.error(detail)

    now = time.time()
    due = (now - st.session_state.last_fetch) >= poll_interval
    if auto_refresh and due:
        st.session_state.last_fetch = now

        if plugin == "all":
            results = fetch_all_packets(cfg)
            if force_demo:
                from trerviz.adapters import demo_packet

                results = {
                    name: type(results[name])(
                        packet=demo_packet(name), ok=False, detail="forced demo"
                    )
                    for name in results
                }
            st.session_state.packets = {k: v.packet for k, v in results.items()}
            st.session_state.fetch_meta = {k: (v.ok, v.detail) for k, v in results.items()}
        else:
            active_cfg = dict(cfg)
            if force_demo:
                sources = dict(active_cfg.get("sources", {}))
                sources[plugin] = {"type": "demo"}
                active_cfg["sources"] = sources
            result = fetch_plugin_packet(plugin, active_cfg)
            st.session_state.packet = result.packet
            st.session_state.fetch_ok = result.ok
            st.session_state.fetch_detail = result.detail

    if plugin == "all":
        packets = st.session_state.get("packets", {})
        fetch_meta = st.session_state.get("fetch_meta", {})
        if not packets:
            st.warning("No packets yet — enable auto refresh or click Refresh now.")
            return
        render_all_summary(packets)
        st.divider()
        cols = st.columns(3)
        for col, (name, packet) in zip(cols, packets.items()):
            ok, detail = fetch_meta.get(name, (False, ""))
            with col:
                st.subheader(name.title())
                render_phase_banner(packet, detail, ok)
                render_metric_cards(packet)
        st.divider()
        selected = st.selectbox("Inspect plugin", list(packets.keys()))
        packet = packets[selected]
        ok, detail = fetch_meta.get(selected, (False, ""))
    else:
        packet = st.session_state.get("packet")
        if packet is None:
            st.warning("No packet yet — enable auto refresh or click Refresh now.")
            return
        ok = st.session_state.get("fetch_ok", False)
        detail = st.session_state.get("fetch_detail", "")
        render_phase_banner(packet, detail, ok)

    history = update_history(
        st.session_state.history,
        packet,
        max_len=int(cfg.get("history_length", 120)),
    )
    st.session_state.history = history

    left, right = st.columns((1, 1))
    with left:
        st.subheader("Pressure & criticality")
        render_time_series(history, packet.plugin)
    with right:
        st.subheader("Pressure field")
        render_field_view(packet)

    st.subheader("Recent events")
    render_events(packet)

    with st.expander("Raw JSON packet", expanded=False):
        render_json_viewer(packet)


if __name__ == "__main__":
    main()
