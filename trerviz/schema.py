"""Normalized visualization packet schema for TRER plugin metrics."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

VIZ_SCHEMA_VERSION = 1

PHASES = (
    "build_up",
    "surge",
    "peak",
    "overextension",
    "discharge",
    "refractory",
    "reorganization",
)

CORE_METRIC_KEYS = (
    "pressure",
    "criticality",
    "velocity",
    "acceleration",
    "dissipation",
    "rupture_risk",
)

# Optional extensions — default 0.0; crow adapter populates coherence/call_density.
OPTIONAL_METRIC_KEYS = (
    "coherence",
    "call_density",
)

METRIC_KEYS = CORE_METRIC_KEYS + OPTIONAL_METRIC_KEYS

SEVERITIES = ("low", "medium", "high")


@dataclass
class VizField:
    id: str
    x: float
    y: float
    pressure: float
    radius: float = 1.0
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VizEvent:
    type: str
    message: str
    severity: str = "low"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VizPacket:
    """Plugin-agnostic packet consumed by the TRER visualizer."""

    schema_version: int
    plugin: str
    timestamp: str
    phase: str
    metrics: dict[str, float]
    fields: list[VizField] = field(default_factory=list)
    events: list[VizEvent] = field(default_factory=list)
    source: str = "unknown"
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plugin": self.plugin,
            "timestamp": self.timestamp,
            "phase": self.phase,
            "metrics": dict(self.metrics),
            "fields": [f.to_dict() for f in self.fields],
            "events": [e.to_dict() for e in self.events],
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VizPacket:
        metrics = empty_metrics()
        for k, v in (data.get("metrics") or {}).items():
            if k in metrics:
                metrics[k] = float(v)
        fields = [
            VizField(
                id=str(item.get("id", "")),
                x=float(item.get("x", 0.0)),
                y=float(item.get("y", 0.0)),
                pressure=float(item.get("pressure", 0.0)),
                radius=float(item.get("radius", 1.0)),
                label=str(item.get("label", "")),
            )
            for item in data.get("fields", [])
        ]
        events = [
            VizEvent(
                type=str(item.get("type", "info")),
                message=str(item.get("message", "")),
                severity=str(item.get("severity", "low")),
            )
            for item in data.get("events", [])
        ]
        return cls(
            schema_version=int(data.get("schema_version", VIZ_SCHEMA_VERSION)),
            plugin=str(data.get("plugin", "unknown")),
            timestamp=str(data.get("timestamp", "")),
            phase=str(data.get("phase", "build_up")),
            metrics=metrics,
            fields=fields,
            events=events,
            source=str(data.get("source", "snapshot")),
            raw=data if isinstance(data.get("raw"), dict) else None,
        )


def empty_metrics() -> dict[str, float]:
    """All known metrics with safe defaults (optional keys included)."""
    return {k: 0.0 for k in METRIC_KEYS}


def merge_metrics(base: dict[str, float], **overrides: float) -> dict[str, float]:
    """Fill core metrics from adapter without dropping optional keys."""
    out = empty_metrics()
    out.update(base)
    for key, value in overrides.items():
        if key in out:
            out[key] = float(value)
    return out


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
