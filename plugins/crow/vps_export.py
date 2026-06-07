"""Optional Blackwing → VPS telemetry export via existing EventClient."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

DEFAULT_EVENT_TYPE = "blackwing.roost.snapshot"
DEFAULT_CONFIDENCE = 0.95
SCHEMA = "blackwing.roost_crow_telemetry.v1"


def telemetry_base_url(blackwing_cfg: dict[str, Any]) -> str:
    tel = blackwing_cfg.get("telemetry", {})
    host = str(tel.get("host", "127.0.0.1"))
    port = int(tel.get("port", 8765))
    return f"http://{host}:{port}"


def fetch_telemetry(base_url: str, *, timeout: float = 5.0) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/api/state"
    req = Request(url, headers={"Accept": "application/json"})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build_roost_snapshot_payload(state: dict[str, Any]) -> dict[str, Any]:
    """Compact derived telemetry — omit fields not present in /api/state."""
    meta = state.get("meta") if isinstance(state.get("meta"), dict) else {}
    lead = state.get("lead") if isinstance(state.get("lead"), dict) else {}
    controls = state.get("controls") if isinstance(state.get("controls"), dict) else {}
    status = state.get("status") if isinstance(state.get("status"), dict) else {}
    colony = state.get("colony") if isinstance(state.get("colony"), dict) else {}
    roost = colony.get("roost") if isinstance(colony.get("roost"), dict) else {}

    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "source": "blackwing-windows",
        "anonymized": True,
        "schema_version": state.get("schema_version"),
        "phase": meta.get("phase"),
        "tracking": meta.get("tracking"),
        "auto_mode": meta.get("auto_mode"),
        "simulation_speed": meta.get("simulation_speed"),
        "flock_count": len(state.get("flock") or []),
    }

    if roost:
        payload["roost"] = {
            k: roost[k]
            for k in ("id", "name", "population", "territory_radius", "location")
            if k in roost
        }
    for key in (
        "daily_cycle",
        "cycle_hour",
        "next_phase",
        "coherence",
        "pressure",
        "phase_deviation",
        "call_density",
        "observed_behavior",
    ):
        if key in colony:
            payload[key] = colony[key]

    if lead:
        lead_out: dict[str, Any] = {}
        if "position" in lead:
            lead_out["position"] = lead["position"]
        if "velocity" in lead:
            lead_out["velocity"] = lead["velocity"]
        if "rotation" in lead:
            lead_out["rotation"] = lead["rotation"]
        if lead_out:
            payload["lead"] = lead_out

    if controls:
        payload["controls"] = {
            k: controls[k]
            for k in ("flap_power", "wingspan", "bank", "pitch", "glide", "perch", "autonomous")
            if k in controls
        }

    if status:
        payload["status"] = {
            k: status[k]
            for k in ("energy", "lift", "drag", "stall_risk")
            if k in status
        }

    flock = state.get("flock")
    if isinstance(flock, list) and flock:
        payload["flock"] = [
            {
                k: bird[k]
                for k in ("id", "position", "mode", "state", "agent_state", "home_roost")
                if k in bird
            }
            for bird in flock[:12]
        ]

    agents = colony.get("agents")
    if isinstance(agents, list) and agents:
        payload["agents"] = [
            {
                k: agent[k]
                for k in ("id", "state", "role", "energy", "distance_home")
                if k in agent
            }
            for agent in agents[:12]
        ]

    return {k: v for k, v in payload.items() if v is not None}


def export_roost_snapshot(
    client: Any,
    state: dict[str, Any],
    *,
    event_type: str = DEFAULT_EVENT_TYPE,
    confidence: float = DEFAULT_CONFIDENCE,
) -> dict[str, Any]:
    payload = build_roost_snapshot_payload(state)
    return client.emit(event_type, confidence, payload)


def try_export_from_url(
    client: Any,
    base_url: str,
    *,
    event_type: str = DEFAULT_EVENT_TYPE,
    confidence: float = DEFAULT_CONFIDENCE,
    timeout: float = 5.0,
) -> dict[str, Any]:
    try:
        state = fetch_telemetry(base_url, timeout=timeout)
    except (URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read Blackwing telemetry at {base_url}/api/state: {exc}") from exc
    return export_roost_snapshot(
        client, state, event_type=event_type, confidence=confidence
    )
