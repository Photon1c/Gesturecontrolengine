"""Blackwing dynamic mode bridge — refresh market snapshots without touching crow physics."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .attractors import MarketAttractorModel
from .market_packet import build_market_packet, write_market_snapshot


class MarketBridge:
    """Optional sidecar for --dynamic Blackwing: CSV attractors → telemetry + snapshot."""

    def __init__(self, cfg: dict[str, Any] | None = None) -> None:
        cfg = cfg or {}
        self._model = MarketAttractorModel(cfg)
        self._refresh_seconds = float(cfg.get("refresh_seconds", 60))
        self._snapshot_path = Path(
            cfg.get("snapshot_path", "trerviz/snapshots/market_latest.json")
        )
        self._last_refresh = 0.0
        self._last_packet: dict[str, Any] | None = None
        self._last_error: str | None = None

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def refresh(self, *, force: bool = False) -> dict[str, Any] | None:
        now = time.time()
        if (
            not force
            and self._last_packet is not None
            and now - self._last_refresh < self._refresh_seconds
        ):
            return self._last_packet
        try:
            state = self._model.compute()
            packet = build_market_packet(state)
            write_market_snapshot(packet, self._snapshot_path)
            self._last_packet = packet
            self._last_error = None
            self._last_refresh = now
            return packet
        except Exception as exc:
            self._last_error = str(exc)
            return self._last_packet

    def telemetry_slice(self) -> dict[str, Any]:
        packet = self.refresh()
        if packet is None:
            return {
                "mode": "dynamic",
                "available": False,
                "error": self._last_error or "market data unavailable",
            }
        hybrid = next(
            (a for a in packet.get("attractors", []) if a.get("source") == "hybrid"),
            {},
        )
        return {
            "mode": "dynamic",
            "available": True,
            "symbol": packet.get("symbol"),
            "phase": packet.get("phase"),
            "spot": packet.get("raw", {}).get("spot"),
            "attractor_position": hybrid.get("position"),
            "attractor_strength": packet.get("metrics", {}).get("attractor_strength"),
            "attractor_velocity": packet.get("metrics", {}).get("attractor_velocity"),
            "attractor_stability": packet.get("metrics", {}).get("attractor_stability"),
            "pressure": packet.get("metrics", {}).get("pressure"),
            "coherence": packet.get("metrics", {}).get("coherence"),
            "snapshot": str(self._snapshot_path),
        }
