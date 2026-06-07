"""Fetch plugin state from HTTP endpoints, snapshot files, or demo mode."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .adapters import adapt_packet, demo_packet
from .schema import VizPacket

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]


DEFAULT_CONFIG: dict[str, Any] = {
    "poll_interval_seconds": 1.0,
    "history_length": 120,
    "snapshot_dir": "trerviz/snapshots",
    "sources": {
        "crow": {
            "type": "http",
            "url": "http://127.0.0.1:8765/api/state",
            "timeout": 1.5,
        },
        "infrastructure": {"type": "demo"},
        "market": {"type": "demo"},
    },
}


@dataclass
class FetchResult:
    packet: VizPacket
    ok: bool
    detail: str = ""


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    if path is None:
        path = Path(__file__).resolve().parent / "config.json"
    cfg_path = Path(path)
    if not cfg_path.is_file():
        return dict(DEFAULT_CONFIG)
    with cfg_path.open(encoding="utf-8") as fh:
        loaded = json.load(fh)
    merged = dict(DEFAULT_CONFIG)
    merged.update(loaded)
    if "sources" in loaded:
        sources = dict(DEFAULT_CONFIG.get("sources", {}))
        sources.update(loaded["sources"])
        merged["sources"] = sources
    return merged


def _read_json_file(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _fetch_http(url: str, timeout: float) -> dict[str, Any] | None:
    if requests is None:
        return None
    try:
        res = requests.get(url, timeout=timeout)
        res.raise_for_status()
        data = res.json()
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _snapshot_paths(snapshot_dir: Path, plugin: str) -> list[Path]:
    return [
        snapshot_dir / f"{plugin}_latest.json",
        snapshot_dir / f"{plugin}.json",
        snapshot_dir / "gepa" / f"{plugin}_snapshot.json",
    ]


def fetch_plugin_packet(
    plugin: str,
    cfg: dict[str, Any] | None = None,
) -> FetchResult:
    """Resolve a plugin packet: HTTP → snapshot file → demo."""
    cfg = cfg or load_config()
    source_cfg = (cfg.get("sources") or {}).get(plugin, {"type": "demo"})
    source_type = str(source_cfg.get("type", "demo")).lower()
    snapshot_dir = Path(cfg.get("snapshot_dir", "trerviz/snapshots"))

    if source_type == "http":
        url = str(source_cfg.get("url", ""))
        timeout = float(source_cfg.get("timeout", 1.5))
        raw = _fetch_http(url, timeout)
        if raw is not None:
            return FetchResult(
                packet=adapt_packet(plugin, raw, source="http"),
                ok=True,
                detail=url,
            )
        for snap_path in _snapshot_paths(snapshot_dir, plugin):
            snap = _read_json_file(snap_path)
            if snap is not None:
                return FetchResult(
                    packet=adapt_packet(plugin, snap, source=f"snapshot:{snap_path.name}"),
                    ok=True,
                    detail=str(snap_path),
                )
        return FetchResult(
            packet=demo_packet(plugin),
            ok=False,
            detail=f"HTTP unavailable ({url}); using demo mode",
        )

    if source_type == "snapshot":
        for snap_path in _snapshot_paths(snapshot_dir, plugin):
            snap = _read_json_file(snap_path)
            if snap is not None:
                return FetchResult(
                    packet=adapt_packet(plugin, snap, source=f"snapshot:{snap_path.name}"),
                    ok=True,
                    detail=str(snap_path),
                )
        return FetchResult(
            packet=demo_packet(plugin),
            ok=False,
            detail="Snapshot missing; using demo mode",
        )

    if source_type == "normalized":
        for snap_path in _snapshot_paths(snapshot_dir, plugin):
            snap = _read_json_file(snap_path)
            if snap is not None:
                return FetchResult(
                    packet=adapt_packet(plugin, snap, source=f"normalized:{snap_path.name}"),
                    ok=True,
                    detail=str(snap_path),
                )
        return FetchResult(
            packet=demo_packet(plugin),
            ok=False,
            detail="Normalized snapshot missing; using demo mode",
        )

    return FetchResult(
        packet=demo_packet(plugin),
        ok=source_type == "demo",
        detail="demo mode",
    )


def crow_control_url(cfg: dict[str, Any] | None = None) -> str:
    """Resolve crow `/api/control` from configured state URL."""
    cfg = cfg or load_config()
    state_url = str((cfg.get("sources") or {}).get("crow", {}).get("url", ""))
    if state_url.endswith("/api/state"):
        return state_url[: -len("/api/state")] + "/api/control"
    base = state_url.rstrip("/")
    return f"{base}/api/control" if base else "http://127.0.0.1:8765/api/control"


def post_crow_control(payload: dict[str, Any], cfg: dict[str, Any] | None = None) -> tuple[bool, str]:
    """POST flock launch commands to the crow telemetry server."""
    if requests is None:
        return False, "requests not installed"
    url = crow_control_url(cfg)
    try:
        res = requests.post(url, json=payload, timeout=1.5)
        res.raise_for_status()
        return True, url
    except Exception as exc:
        return False, f"{url}: {exc}"


def fetch_all_packets(cfg: dict[str, Any] | None = None) -> dict[str, FetchResult]:
    cfg = cfg or load_config()
    plugins = list((cfg.get("sources") or DEFAULT_CONFIG["sources"]).keys())
    return {plugin: fetch_plugin_packet(plugin, cfg) for plugin in plugins}
