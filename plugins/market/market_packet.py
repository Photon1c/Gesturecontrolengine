"""TRER-compatible normalized market packet builder."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .attractors import MarketAttractorState


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_market_packet(state: MarketAttractorState) -> dict[str, Any]:
    """Emit normalized packet matching docs/blackwing-upgrade.md."""
    symbol = state.symbol
    hybrid_id = f"{symbol.lower()}_hybrid_attractor"
    scale = max(abs(state.spot), 1.0)

    fields = [
        {
            "id": symbol,
            "x": 0.0,
            "y": 0.0,
            "pressure": round(state.pressure, 4),
            "radius": 1.0,
            "label": symbol,
        },
        {
            "id": hybrid_id,
            "x": round((state.hybrid_attractor - state.spot) / scale * 10.0, 4),
            "y": round(state.distance_from_attractor / scale * 10.0, 4),
            "pressure": round(state.attractor_strength, 4),
            "radius": round(0.8 + state.attractor_stability * 0.8, 4),
            "label": "Hybrid attractor",
        },
    ]

    return {
        "schema_version": 1,
        "plugin": "market",
        "symbol": symbol,
        "timestamp": _iso_now(),
        "phase": state.phase,
        "metrics": {
            "pressure": round(state.pressure, 4),
            "coherence": round(state.coherence, 4),
            "criticality": round(state.criticality, 4),
            "dissipation": round(state.dissipation, 4),
            "velocity": round(state.velocity, 4),
            "acceleration": round(state.acceleration, 4),
            "rupture_risk": round(state.rupture_risk, 4),
            "attractor_strength": round(state.attractor_strength, 4),
            "attractor_velocity": round(state.attractor_velocity, 4),
            "attractor_stability": round(state.attractor_stability, 4),
        },
        "attractors": [
            {
                "id": a.id,
                "type": a.type,
                "position": round(a.position, 4),
                "strength": round(a.strength, 4),
                "velocity": round(a.velocity, 4),
                "stability": round(a.stability, 4),
                "source": a.source,
            }
            for a in state.attractors
        ],
        "fields": fields,
        "events": [],
        "raw": {
            "spot": round(state.spot, 4),
            "price_attractor": round(state.price_attractor, 4),
            "options_attractor": round(state.options_attractor, 4)
            if state.options_attractor is not None
            else None,
            "hybrid_attractor": round(state.hybrid_attractor, 4),
            "distance_from_attractor": round(state.distance_from_attractor, 4),
            **state.raw,
        },
    }


def write_market_snapshot(packet: dict[str, Any], path: str | Path) -> Path:
    import json

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(packet, indent=2), encoding="utf-8")
    return out


def build_and_write_snapshot(
    cfg: dict[str, Any] | None = None,
    *,
    snapshot_path: str | Path = "trerviz/snapshots/market_latest.json",
) -> dict[str, Any]:
    from .attractors import compute_attractors

    state = compute_attractors(cfg)
    packet = build_market_packet(state)
    write_market_snapshot(packet, snapshot_path)
    return packet
