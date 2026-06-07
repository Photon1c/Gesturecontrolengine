"""Streamlit dashboard panels for TRER visualization packets."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from .schema import METRIC_KEYS, VizPacket

try:
    import streamlit as st
except ImportError:  # pragma: no cover
    st = None  # type: ignore[assignment]


def _format_metric(key: str, value: float) -> str:
    if key == "call_density":
        return f"{value:.0f}/min"
    return f"{value:.2f}"


def render_metric_cards(packet: VizPacket) -> None:
    keys = list(METRIC_KEYS)
    for row_start in range(0, len(keys), 4):
        row = keys[row_start : row_start + 4]
        cols = st.columns(len(row))
        for col, key in zip(cols, row):
            value = float(packet.metrics.get(key, 0.0))
            col.metric(
                label=key.replace("_", " ").title(),
                value=_format_metric(key, value),
                delta=None,
            )


def render_phase_banner(packet: VizPacket, source_detail: str, live: bool) -> None:
    status = "live" if live else "demo / fallback"
    st.caption(f"Source: {packet.source} ({status}) — {source_detail}")
    st.markdown(f"**Phase:** `{packet.phase}` · **Plugin:** `{packet.plugin}`")


def update_history(
    history: dict[str, list[dict[str, Any]]],
    packet: VizPacket,
    *,
    max_len: int,
) -> dict[str, list[dict[str, Any]]]:
    row = {
        "timestamp": packet.timestamp,
        "pressure": packet.metrics.get("pressure", 0.0),
        "criticality": packet.metrics.get("criticality", 0.0),
        "velocity": packet.metrics.get("velocity", 0.0),
        "rupture_risk": packet.metrics.get("rupture_risk", 0.0),
    }
    hist = history.setdefault(packet.plugin, [])
    prev_velocity = hist[-1]["velocity"] if hist else row["velocity"]
    row["acceleration"] = row["velocity"] - prev_velocity
    packet.metrics["acceleration"] = row["acceleration"]

    hist.append(row)
    if len(hist) > max_len:
        history[packet.plugin] = hist[-max_len:]
    return history


def render_time_series(history: dict[str, list[dict[str, Any]]], plugin: str) -> None:
    rows = history.get(plugin, [])
    if not rows:
        st.info("Waiting for samples…")
        return
    df = pd.DataFrame(rows)
    if "timestamp" in df.columns:
        df = df.set_index("timestamp")
    st.line_chart(df[["pressure", "criticality"]], height=260)


def render_field_view(packet: VizPacket) -> None:
    if not packet.fields:
        st.info("No field nodes in packet.")
        return
    rows = [
        {
            "id": f.id,
            "x": f.x,
            "y": f.y,
            "pressure": f.pressure,
            "radius": f.radius,
            "label": f.label,
        }
        for f in packet.fields
    ]
    df = pd.DataFrame(rows)
    st.scatter_chart(
        df,
        x="x",
        y="y",
        size="radius",
        color="pressure",
        height=360,
    )
    st.dataframe(df, use_container_width=True, hide_index=True)


def render_events(packet: VizPacket) -> None:
    if not packet.events:
        st.caption("No recent events.")
        return
    for event in reversed(packet.events[-8:]):
        severity = event.severity
        icon = {"high": "🔴", "medium": "🟠", "low": "🟢"}.get(severity, "⚪")
        st.markdown(f"{icon} **{event.type}** — {event.message}")


def render_json_viewer(packet: VizPacket) -> None:
    payload = packet.to_dict()
    if packet.raw is not None:
        payload["raw"] = packet.raw
    st.code(json.dumps(payload, indent=2), language="json")


def render_all_summary(packets: dict[str, VizPacket]) -> None:
    rows = []
    for plugin, packet in packets.items():
        rows.append(
            {
                "plugin": plugin,
                "phase": packet.phase,
                "pressure": packet.metrics.get("pressure", 0.0),
                "criticality": packet.metrics.get("criticality", 0.0),
                "rupture_risk": packet.metrics.get("rupture_risk", 0.0),
                "source": packet.source,
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
