"""Environmental interaction layer — resource nodes, threats, sentinel alerts, escape."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .colony_cycle import SCHEDULE_OUTBOUND, SCHEDULE_PATROL
from .physics import CrowState

_RESOURCE_PHASES = SCHEDULE_PATROL | SCHEDULE_OUTBOUND | frozenset({"foraging"})


@dataclass
class EnvNode:
    id: str
    type: str
    x: float
    y: float
    z: float
    attractor_strength: float = 0.5
    radius: float = 12.0
    source: str | None = None
    label: str = ""
    resource_type: str | None = None
    capacity: int = 0
    recharge_rate: float = 1.0
    social_rule: str = "open"
    pressure_effect: float = 0.0
    coherence_effect: float = 0.0
    access_radius: float = 2.2
    wait_radius: float = 6.5
    drink_duration: float = 2.5

    @property
    def location(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)

    @property
    def is_capacity_limited(self) -> bool:
        return self.type == "resource" and self.capacity > 0

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "name": self.label or self.id,
            "location": [self.x, self.y, self.z],
            "attractor_strength": round(self.attractor_strength, 3),
            "radius": round(self.radius, 2),
        }
        if self.source:
            payload["source"] = self.source
        if self.resource_type:
            payload["resource_type"] = self.resource_type
        if self.capacity > 0:
            payload["capacity"] = self.capacity
            payload["recharge_rate"] = round(self.recharge_rate, 3)
            payload["social_rule"] = self.social_rule
            payload["pressure_effect"] = round(self.pressure_effect, 3)
            payload["coherence_effect"] = round(self.coherence_effect, 3)
            payload["access_radius"] = round(self.access_radius, 2)
            payload["wait_radius"] = round(self.wait_radius, 2)
        return payload


@dataclass
class ResourceSiteState:
    occupant_id: str | None = None
    occupant_until: float = 0.0
    queue: list[str] = field(default_factory=list)
    visitors: set[str] = field(default_factory=set)
    last_turn_change: float = 0.0


def _dist_xz(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.hypot(a[0] - b[0], a[2] - b[2])


def _parse_node(entry: dict[str, Any]) -> EnvNode:
    loc = entry.get("location", [0, 10, 0])
    strength = float(entry.get("attractor_strength", entry.get("strength", 0.5)))
    return EnvNode(
        id=str(entry.get("id", "node")),
        type=str(entry.get("type", "resource")),
        x=float(loc[0]),
        y=float(loc[1]),
        z=float(loc[2]),
        attractor_strength=strength,
        radius=float(entry.get("radius", 12.0)),
        source=entry.get("source"),
        label=str(entry.get("name", entry.get("label", entry.get("id", "")))),
        resource_type=entry.get("resource_type"),
        capacity=int(entry.get("capacity", 0) or 0),
        recharge_rate=float(entry.get("recharge_rate", 1.0)),
        social_rule=str(entry.get("social_rule", "open")),
        pressure_effect=float(entry.get("pressure_effect", 0.0)),
        coherence_effect=float(entry.get("coherence_effect", 0.0)),
        access_radius=float(entry.get("access_radius", 2.2)),
        wait_radius=float(entry.get("wait_radius", 6.5)),
        drink_duration=float(entry.get("drink_duration", 2.5)),
    )


class EnvironmentLayer:
    """Map nodes (resource/threat) + sentinel alert propagation + coordinated escape."""

    ALERT_NONE = 0
    ALERT_DETECTED = 1
    ALERT_PROPAGATED = 2

    WATCHER_ROLES = frozenset({"scout", "sentinel"})

    def __init__(self, cfg: dict[str, Any] | None = None) -> None:
        cfg = cfg or {}
        self.enabled = bool(cfg.get("enabled", True))
        self._nodes: list[EnvNode] = []
        for entry in cfg.get("nodes") or []:
            if isinstance(entry, dict):
                self._nodes.append(_parse_node(entry))

        sentinel = cfg.get("sentinel") if isinstance(cfg.get("sentinel"), dict) else {}
        self._alert_radius = float(sentinel.get("alert_radius", 15.0))
        self._reaction_time_s = float(sentinel.get("reaction_time_ms", 120)) / 1000.0
        self._propagation_s = float(sentinel.get("propagation_ms", 280)) / 1000.0

        escape = cfg.get("escape") if isinstance(cfg.get("escape"), dict) else {}
        self._escape_duration = float(escape.get("duration_seconds", 8.0))
        self._escape_distance = float(escape.get("flee_distance", 28.0))
        self._scatter_spread = float(escape.get("scatter_spread", 4.5))

        self._site_states: dict[str, ResourceSiteState] = {
            n.id: ResourceSiteState() for n in self._nodes if n.type == "resource"
        }
        self._crow_roles: dict[str, str] = {}
        self._pressure_modifier = 0.0
        self._coherence_modifier = 0.0
        self._convergence_count = 0

        self._alert_level = self.ALERT_NONE
        self._alert_timer = 0.0
        self._alert_origin_time = 0.0
        self._escape_until = 0.0
        self._threat_active = False
        self._at_resource = False
        self._resource_visit_active = False
        self._detector_id: str | None = None
        self._information_velocity = 0.0
        self._events: list[dict[str, str]] = []

    def _resource_nodes(self) -> list[EnvNode]:
        return [n for n in self._nodes if n.type == "resource"]

    def _threat_node(self) -> EnvNode | None:
        for node in self._nodes:
            if node.type == "threat":
                return node
        return None

    def _schedule_allows_resources(self, schedule_phase: str) -> bool:
        return schedule_phase in _RESOURCE_PHASES

    def threat_position(self) -> tuple[float, float, float] | None:
        node = self._threat_node()
        return node.location if node else None

    def escape_active(self, sim_clock: float) -> bool:
        if not self.enabled:
            return False
        return (
            self._alert_level >= self.ALERT_PROPAGATED
            and sim_clock < self._escape_until
            and self._threat_active
        )

    def alert_active(self) -> bool:
        return self._alert_level >= self.ALERT_DETECTED

    def metric_modifiers(self) -> tuple[float, float]:
        return self._pressure_modifier, self._coherence_modifier

    def resource_target(
        self, schedule_phase: str, crow: CrowState | None = None
    ) -> tuple[float, float, float] | None:
        if crow is not None:
            return self.resource_target_for(crow, schedule_phase)
        nodes = self._resource_nodes()
        if not nodes:
            return None
        best = max(nodes, key=lambda n: n.attractor_strength)
        return best.location

    def resource_target_for(
        self, crow: CrowState, schedule_phase: str
    ) -> tuple[float, float, float] | None:
        if not self.enabled or not self._schedule_allows_resources(schedule_phase):
            return None
        if self.alert_active():
            return None

        role = self._crow_roles.get(crow.id)
        if role == "drinking":
            for node in self._resource_nodes():
                site = self._site_states.get(node.id)
                if site and site.occupant_id == crow.id:
                    return (node.x, node.y - 0.35, node.z)
        if role == "waiting":
            for node in self._resource_nodes():
                site = self._site_states.get(node.id)
                if site and crow.id in site.queue:
                    idx = site.queue.index(crow.id)
                    return self._wait_slot(node, idx)

        node = self._pick_resource_node(crow, schedule_phase)
        if node is None:
            return None
        site = self._site_states.get(node.id)
        if node.is_capacity_limited and site and crow.id in site.queue:
            return self._wait_slot(node, site.queue.index(crow.id))
        return node.location

    def _pick_resource_node(self, crow: CrowState, schedule_phase: str) -> EnvNode | None:
        nodes = [n for n in self._resource_nodes() if self._schedule_allows_resources(schedule_phase)]
        if not nodes:
            return None
        best: EnvNode | None = None
        best_score = -1.0
        for node in nodes:
            dist = _dist_xz((crow.x, crow.y, crow.z), node.location)
            if dist > node.radius * 1.35 and best is not None:
                continue
            site = self._site_states.get(node.id)
            visitor_boost = 1.0 + 0.08 * len(site.visitors) if site else 1.0
            score = node.attractor_strength * visitor_boost / (1.0 + dist * 0.015)
            if node.resource_type == "water":
                score *= 1.12
            if score > best_score:
                best_score = score
                best = node
        return best or max(nodes, key=lambda n: n.attractor_strength)

    def _wait_slot(self, node: EnvNode, queue_index: int) -> tuple[float, float, float]:
        angle = (queue_index + 1) * 1.35
        r = node.wait_radius * (0.75 + 0.08 * (queue_index % 3))
        return (
            node.x + math.sin(angle) * r,
            node.y + 0.15,
            node.z + math.cos(angle) * r,
        )

    def escape_target(self, crow: CrowState) -> tuple[float, float, float]:
        threat = self.threat_position()
        if threat is None:
            return (crow.x + 10.0, crow.y + 2.0, crow.z - 10.0)
        dx = crow.x - threat[0]
        dz = crow.z - threat[2]
        dist = math.hypot(dx, dz)
        if dist < 1e-6:
            dx, dz = 1.0, -0.5
            dist = math.hypot(dx, dz)
        scale = self._escape_distance / dist
        flee_y = max(crow.y + 1.5, threat[1] + 3.0)
        return (
            crow.x + (dx / dist) * self._escape_distance * scale * 0.85,
            flee_y,
            crow.z + (dz / dist) * self._escape_distance * scale * 0.85,
        )

    def escape_vector(
        self, crow: CrowState, threat: tuple[float, float, float]
    ) -> tuple[float, float]:
        dx = crow.x - threat[0]
        dz = crow.z - threat[2]
        dist = math.hypot(dx, dz)
        if dist < 1e-6:
            return (1.0, -0.4)
        return (dx / dist, dz / dist)

    def escape_guidance(
        self, crow: CrowState, dt: float, *, strength: float = 6.5
    ) -> tuple[float, float]:
        threat = self.threat_position()
        if threat is None:
            return (0.0, 0.0)
        ex, ez = self.escape_vector(crow, threat)
        return ex * strength * dt, ez * strength * dt

    def scatter_target(
        self, follower: CrowState, index: int, lead: CrowState
    ) -> tuple[float, float, float]:
        threat = self.threat_position()
        base = self.escape_target(follower)
        if threat is None:
            return base
        side = self._scatter_spread * (1 if index % 2 else -1) * ((index + 1) // 2)
        ex, ez = self.escape_vector(follower, threat)
        px, pz = -ez, ex
        return (base[0] + px * side, base[1] + index * 0.25, base[2] + pz * side)

    def agent_state_override(
        self,
        agent: Any,
        *,
        sim_clock: float,
        player_control: bool,
    ) -> str | None:
        if not self.enabled or player_control:
            return None
        if self.escape_active(sim_clock):
            return "ESCAPE"
        role = self._crow_roles.get(agent.crow_id)
        if role == "drinking":
            return "DRINKING"
        if role == "waiting":
            return "WAITING"
        if self._alert_level == self.ALERT_DETECTED and agent.role in self.WATCHER_ROLES:
            return "ALARMED"
        if self._alert_level == self.ALERT_DETECTED and agent.crow_id == self._detector_id:
            return "ALARMED"
        return None

    def _update_resource_sites(
        self,
        crows: list[CrowState],
        agents: dict[str, Any],
        *,
        schedule_phase: str,
        sim_clock: float,
        sim_dt: float,
    ) -> None:
        self._crow_roles.clear()
        self._pressure_modifier = 0.0
        self._coherence_modifier = 0.0
        self._convergence_count = 0

        if not self._schedule_allows_resources(schedule_phase) or self.alert_active():
            for site in self._site_states.values():
                site.visitors.clear()
                site.queue.clear()
                site.occupant_id = None
            return

        for node in self._resource_nodes():
            site = self._site_states.setdefault(node.id, ResourceSiteState())
            site.visitors.clear()

            nearby: list[tuple[float, str, int]] = []
            for crow in crows:
                dist = _dist_xz((crow.x, crow.y, crow.z), node.location)
                if dist <= node.radius:
                    site.visitors.add(crow.id)
                    agent = agents.get(crow.id)
                    order = agent.spawn_order if agent else 99
                    nearby.append((dist, crow.id, order))

            if not node.is_capacity_limited or node.social_rule != "turn_taking":
                continue

            nearby_sorted = sorted(nearby, key=lambda t: (t[0], t[2]))
            candidate_queue = [crow_id for _, crow_id, _ in nearby_sorted]

            if site.occupant_id and sim_clock >= site.occupant_until:
                prev = site.occupant_id
                site.occupant_id = None
                site.occupant_until = 0.0
                if sim_clock - site.last_turn_change > 0.5:
                    self._events.append(
                        {
                            "type": "turn_taking",
                            "message": f"Turn complete at {node.label or node.id}",
                            "severity": "low",
                        }
                    )
                    site.last_turn_change = sim_clock

            if site.occupant_id is None and candidate_queue:
                site.occupant_id = candidate_queue[0]
                site.occupant_until = sim_clock + node.drink_duration / max(
                    node.recharge_rate, 0.1
                )

            site.queue = [c for c in candidate_queue if c != site.occupant_id]

            for crow_id in site.visitors:
                if crow_id == site.occupant_id:
                    self._crow_roles[crow_id] = "drinking"
                else:
                    self._crow_roles[crow_id] = "waiting"

            if len(site.visitors) >= 2 and node.resource_type == "water":
                self._convergence_count = max(self._convergence_count, len(site.visitors))
                if len(site.visitors) >= 2 and not any(
                    e.get("type") == "water_convergence" for e in self._events
                ):
                    self._events.append(
                        {
                            "type": "water_convergence",
                            "message": f"Crows converging at {node.label or node.id}",
                            "severity": "low",
                        }
                    )

            if site.occupant_id and node.resource_type == "water":
                self._pressure_modifier += node.pressure_effect
                self._coherence_modifier += node.coherence_effect * min(
                    1.0, len(site.visitors) / max(node.capacity + 2, 2)
                )

        self._pressure_modifier = max(-0.35, min(0.35, self._pressure_modifier))
        self._coherence_modifier = max(-0.2, min(0.35, self._coherence_modifier))

    def update(
        self,
        crows: list[CrowState],
        agents: dict[str, Any],
        *,
        schedule_phase: str,
        player_control: bool,
        player_tracking: bool,
        sim_clock: float,
        sim_dt: float,
    ) -> None:
        self._events = []
        if not self.enabled:
            return

        self._at_resource = False
        if self._schedule_allows_resources(schedule_phase):
            for node in self._resource_nodes():
                for crow in crows:
                    if _dist_xz((crow.x, crow.y, crow.z), node.location) <= node.radius:
                        self._at_resource = True
                        break
                if self._at_resource:
                    break
            if self._at_resource:
                self._resource_visit_active = True

        self._update_resource_sites(
            crows, agents, schedule_phase=schedule_phase, sim_clock=sim_clock, sim_dt=sim_dt
        )

        threat = self._threat_node()
        prev_threat = self._threat_active
        self._threat_active = bool(threat and player_tracking and not player_control)
        rising_threat = self._threat_active and not prev_threat

        if rising_threat and self._resource_visit_active:
            self._events.append(
                {
                    "type": "resource_visit_interrupted",
                    "message": "Resource visit interrupted by disturbance",
                    "severity": "medium",
                }
            )

        if self._threat_active and self._alert_level == self.ALERT_NONE:
            threat_pos = threat.location if threat else (0.0, 0.0, 0.0)
            detector_id: str | None = None
            for crow in crows:
                agent = agents.get(crow.id)
                if agent is None or agent.role not in self.WATCHER_ROLES:
                    continue
                reach = self._alert_radius + (threat.radius if threat else 0.0)
                if _dist_xz((crow.x, crow.y, crow.z), threat_pos) <= reach:
                    detector_id = crow.id
                    break
            if rising_threat and (self._at_resource or detector_id):
                self._alert_level = self.ALERT_DETECTED
                self._alert_timer = 0.0
                self._alert_origin_time = sim_clock
                self._detector_id = detector_id or (crows[0].id if crows else None)
                for site in self._site_states.values():
                    site.occupant_id = None
                    site.queue.clear()

        if self._alert_level == self.ALERT_DETECTED:
            self._alert_timer += sim_dt
            if self._alert_timer >= self._reaction_time_s:
                self._alert_level = self.ALERT_PROPAGATED
                self._escape_until = sim_clock + self._escape_duration
                elapsed = max(self._alert_timer, 1e-6)
                self._information_velocity = round(
                    min(1.0, (self._propagation_s / elapsed) * 0.35 + 0.45), 3
                )
                self._events.append(
                    {
                        "type": "colony_alert",
                        "message": "Sentinel alert propagated — flock scattering",
                        "severity": "high",
                    }
                )
                if self._resource_visit_active:
                    self._events.append(
                        {
                            "type": "stress_evacuation",
                            "message": "Stress evacuation during escape",
                            "severity": "low",
                        }
                    )

        if not self._threat_active and sim_clock >= self._escape_until:
            self._alert_level = self.ALERT_NONE
            self._alert_timer = 0.0
            self._detector_id = None
            if not self._at_resource:
                self._resource_visit_active = False

    @property
    def information_velocity(self) -> float:
        return self._information_velocity

    @property
    def events(self) -> list[dict[str, str]]:
        return list(self._events)

    def site_telemetry(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for node in self._resource_nodes():
            if not node.is_capacity_limited:
                continue
            site = self._site_states.get(node.id)
            if site is None:
                continue
            out.append(
                {
                    "id": node.id,
                    "resource_type": node.resource_type,
                    "occupant": site.occupant_id,
                    "queue": [c for c in site.queue if c != site.occupant_id],
                    "visitors": len(site.visitors),
                    "social_rule": node.social_rule,
                }
            )
        return out

    def attractors_telemetry(self) -> list[dict[str, Any]]:
        return [n.to_dict() for n in self._nodes if n.type in ("fixed", "resource", "threat")]

    def to_telemetry(self, sim_clock: float) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "at_resource": self._at_resource,
            "resource_visit_active": self._resource_visit_active,
            "threat_active": self._threat_active,
            "alert_level": self._alert_level,
            "escape_active": self.escape_active(sim_clock),
            "information_velocity": self._information_velocity,
            "detector_id": self._detector_id,
            "convergence_count": self._convergence_count,
            "pressure_modifier": round(self._pressure_modifier, 3),
            "coherence_modifier": round(self._coherence_modifier, 3),
            "nodes": [n.to_dict() for n in self._nodes],
            "attractors": self.attractors_telemetry(),
            "resource_sites": self.site_telemetry(),
            "events": self.events,
        }
