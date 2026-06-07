"""Roost + colony agent model — crows as population, not particles."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

from .colony_cycle import (
    SCHEDULE_NEAR_ROOST,
    SCHEDULE_OUTBOUND,
    SCHEDULE_PATROL,
    SCHEDULE_RETURN,
    SCHEDULE_ROOST_PERCH,
    acoustic_pressure,
    PHASE_NOTES,
    colony_event_hints,
    combine_trait_modifiers,
    compute_coherence,
    compute_pressure,
    estimate_call_density,
    flock_dispersion,
    infer_observed_behavior,
    legacy_phase,
    next_phase,
    phase_deviation,
    roost_proximity,
    schedule_phase,
    trait_scalar,
)
from .physics import CrowState


AGENT_STATES = (
    "PERCHING",
    "FORAGING",
    "PATROLLING",
    "RETURNING",
    "FOLLOWING_PLAYER",
    "ALARMED",
)

DAILY_PHASES = (
    "night_roost",
    "morning_departure",
    "midday_forage",
    "evening_return",
)

# Backward-compatible alias for legacy 4-phase helpers.
LEGACY_DAILY_PHASES = DAILY_PHASES


@dataclass
class Roost:
    id: str
    name: str
    x: float
    y: float
    z: float
    population: int
    food_score: float
    safety_score: float
    noise_level: float
    territory_radius: float
    pilot_name: str = "Pilot"
    pilot_trust: float = 0.85
    lat: float | None = None
    lon: float | None = None
    traits: tuple[str, ...] = ()
    active: bool = True

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "location": [round(self.x, 2), round(self.y, 2), round(self.z, 2)],
            "population": self.population,
            "food_score": round(self.food_score, 3),
            "safety_score": round(self.safety_score, 3),
            "noise_level": round(self.noise_level, 3),
            "territory_radius": round(self.territory_radius, 2),
            "pilot_name": self.pilot_name,
            "pilot_trust": round(self.pilot_trust, 3),
            "traits": list(self.traits),
            "active": self.active,
        }
        if self.lat is not None and self.lon is not None:
            out["geo"] = {"lat": self.lat, "lon": self.lon}
        return out


@dataclass
class CrowAgent:
    crow_id: str
    home_roost_id: str
    energy: float
    curiosity: float
    state: str = "PERCHING"
    role: str = "wing"
    sex: str = "unknown"
    spawn_order: int = 1


def daily_phase(hour: float) -> str:
    if 5.0 <= hour < 9.0:
        return "morning_departure"
    if 9.0 <= hour < 17.0:
        return "midday_forage"
    if 17.0 <= hour < 21.0:
        return "evening_return"
    return "night_roost"


def resolve_cycle_hour(ts: float, cfg: dict[str, Any]) -> float:
    """Wall clock, frozen hour, or compressed day loop for stable colony routines."""
    if cfg.get("simulated_hour") is not None:
        return float(cfg["simulated_hour"])
    compressed = float(cfg.get("compressed_cycle_minutes", 0) or 0)
    if compressed > 0:
        frac = (ts % (compressed * 60.0)) / (compressed * 60.0)
        return frac * 24.0
    return (ts % 86400.0) / 3600.0


class Colony:
    """Roost-level colony model — schedule, traits, coherence, pressure."""

    def __init__(self, cfg: dict[str, Any]) -> None:
        self._cycle_cfg = cfg.get("daily_cycle", {})
        self._roosts = self._load_roosts(cfg)
        self.roost = self._pick_home_roost(cfg)
        self._trait_mods = combine_trait_modifiers(list(self.roost.traits))
        self._agents: dict[str, CrowAgent] = {}
        self._greeted = False
        self._phase_note = "The roost is quiet."
        self._schedule_phase = "patrol"
        self._legacy_phase = "midday_forage"
        self._cycle_hour = 12.0
        self._observed_behavior = "patrolling"
        self._phase_deviation = 0.0
        self._coherence = 0.5
        self._pressure = 0.0
        self._call_density = 0.0
        self._acoustic_pressure = 0.0
        self._colony_events: list[dict[str, str]] = []
        self._waypoint_index = 0
        self._waypoint_dwell = 0.0
        self._patrol_points = self._build_patrol_points()

    def _roost_from_entry(self, entry: dict[str, Any]) -> Roost:
        loc = entry.get("location", [0, 8, 0])
        geo = entry.get("geo", {})
        traits = tuple(t for t in entry.get("traits", []) if isinstance(t, str))
        return Roost(
            id=str(entry.get("id", "north_yard")),
            name=str(entry.get("name", "Roost")),
            x=float(loc[0]),
            y=float(loc[1]),
            z=float(loc[2]),
            population=int(entry.get("population", 20)),
            food_score=float(entry.get("food_score", 0.72)),
            safety_score=float(entry.get("safety_score", 0.85)),
            noise_level=float(entry.get("noise_level", 0.15)),
            territory_radius=float(entry.get("territory_radius", 45)),
            pilot_name=str(entry.get("pilot_name", "Pilot")),
            pilot_trust=float(entry.get("pilot_trust", 0.85)),
            lat=geo.get("lat"),
            lon=geo.get("lon"),
            traits=traits,
            active=bool(entry.get("active", True)),
        )

    def _load_roosts(self, cfg: dict[str, Any]) -> list[Roost]:
        entries = cfg.get("roosts")
        if isinstance(entries, list) and entries:
            return [self._roost_from_entry(e) for e in entries if isinstance(e, dict)]
        return [self._roost_from_entry(cfg)]

    def _pick_home_roost(self, cfg: dict[str, Any]) -> Roost:
        home_id = str(cfg.get("home_roost_id", cfg.get("id", "north_yard")))
        for roost in self._roosts:
            if roost.id == home_id and roost.active:
                return roost
        for roost in self._roosts:
            if roost.active:
                return roost
        return self._roosts[0]

    @property
    def cycle_phase(self) -> str:
        """Legacy 4-phase label — used by flight routines (unchanged physics path)."""
        return self._legacy_phase

    @property
    def schedule_phase(self) -> str:
        return self._schedule_phase

    def _build_patrol_points(self) -> list[tuple[float, float, float]]:
        bearings = self._cycle_cfg.get(
            "patrol_bearings", [0, 60, 120, 180, 240, 300]
        )
        radius_frac = float(self._cycle_cfg.get("patrol_radius_fraction", 0.52))
        radius_frac *= 1.0 + trait_scalar(self._trait_mods, "patrol_radius")
        altitude = float(self._cycle_cfg.get("patrol_altitude", 14.0))
        dist = self.roost.territory_radius * radius_frac
        points: list[tuple[float, float, float]] = []
        for bearing in bearings:
            angle = math.radians(float(bearing))
            points.append(
                (
                    self.roost.x + math.sin(angle) * dist,
                    altitude,
                    self.roost.z + math.cos(angle) * dist,
                )
            )
        return points

    def _patrol_point(self, index: int) -> tuple[float, float, float]:
        if not self._patrol_points:
            return (self.roost.x, self.roost.y + 5.0, self.roost.z + 8.0)
        return self._patrol_points[index % len(self._patrol_points)]

    def reset_patrol_state(self) -> None:
        self._waypoint_index = 0
        self._waypoint_dwell = 0.0

    def roost_perch_target(self) -> tuple[float, float, float]:
        return self._roost_perch_target()

    def roost_approach_target(self) -> tuple[float, float, float]:
        return self._roost_approach_target()

    def wants_roost_perch(self) -> bool:
        return self._schedule_phase in SCHEDULE_ROOST_PERCH

    def homing_to_roost(self, crow: CrowState) -> bool:
        if self._schedule_phase in SCHEDULE_ROOST_PERCH:
            return True
        if self._schedule_phase in SCHEDULE_RETURN:
            radius = float(self._cycle_cfg.get("return_perch_homing_radius", 24.0))
            return self.distance_to_roost(crow) < radius
        return False

    def perch_target_for(
        self, crow_id: str, fallback_index: int = 0
    ) -> tuple[float, float, float]:
        base = self._roost_perch_target()
        agent = self._agents.get(crow_id)
        idx = (agent.spawn_order - 1) if agent else fallback_index
        offsets = (
            (0.0, 0.0, 0.0),
            (-1.5, 0.12, 1.0),
            (1.5, 0.12, -1.0),
            (-0.9, 0.28, -1.2),
            (1.1, 0.22, 1.3),
            (-2.0, 0.08, -0.4),
            (2.0, 0.08, 0.4),
        )
        off = offsets[idx % len(offsets)]
        return (base[0] + off[0], base[1] + off[1], base[2] + off[2])

    def _roost_perch_target(self) -> tuple[float, float, float]:
        roost_y = self.roost.y + float(self._cycle_cfg.get("roost_perch_height", 2.0))
        return (self.roost.x, roost_y, self.roost.z)

    def _roost_approach_target(self) -> tuple[float, float, float]:
        approach_y = self.roost.y + float(self._cycle_cfg.get("roost_approach_height", 5.0))
        return (self.roost.x, approach_y, self.roost.z + 5.0)

    def _patrol_transit_target(
        self, crow: CrowState, sim_dt: float
    ) -> tuple[float, float, float]:
        """Two-anchor patrol legs with dwell — avoids tight hex orbits."""
        inner = float(self._cycle_cfg.get("waypoint_arrival_inner", 8.0))
        dwell_req = float(self._cycle_cfg.get("waypoint_dwell_seconds", 4.0))
        n = max(len(self._patrol_points), 1)
        target = self._patrol_point(self._waypoint_index)
        dist = math.hypot(crow.x - target[0], crow.z - target[2])

        if dist < inner:
            self._waypoint_dwell += sim_dt
        else:
            self._waypoint_dwell = max(0.0, self._waypoint_dwell - sim_dt * 0.25)

        if self._waypoint_dwell >= dwell_req:
            half = max(n // 2, 1)
            self._waypoint_index = (
                half if self._waypoint_index == 0 else 0
            ) % n
            self._waypoint_dwell = 0.0
            target = self._patrol_point(self._waypoint_index)
        return target

    def routine_target(self, crow: CrowState, sim_dt: float = 0.033) -> tuple[float, float, float]:
        """Schedule-aware waypoint — perch, return, outbound, or patrol transit."""
        if self._schedule_phase in SCHEDULE_ROOST_PERCH:
            return self._roost_perch_target()

        if self._schedule_phase in SCHEDULE_RETURN:
            return self._roost_approach_target()

        if self._schedule_phase in SCHEDULE_NEAR_ROOST:
            call_y = self.roost.y + float(self._cycle_cfg.get("morning_call_height", 6.0))
            return (self.roost.x, call_y, self.roost.z + 10.0)

        if self._schedule_phase in SCHEDULE_OUTBOUND:
            return self._patrol_point(0)

        if self._schedule_phase in SCHEDULE_PATROL:
            return self._patrol_transit_target(crow, sim_dt)

        return self._roost_perch_target()

    def _auto_agent_state(self, dist_home: float) -> str:
        phase = self._schedule_phase
        if phase in SCHEDULE_ROOST_PERCH:
            return "PERCHING"
        if phase in SCHEDULE_RETURN:
            return "RETURNING"
        if phase in SCHEDULE_NEAR_ROOST:
            return "PERCHING"
        if phase in SCHEDULE_OUTBOUND:
            return "FORAGING" if dist_home > 10 else "PERCHING"
        if phase == "foraging":
            return "FORAGING"
        if phase == "patrol":
            return "PATROLLING"
        return "PATROLLING"

    def get_agent(self, crow_id: str) -> CrowAgent | None:
        return self._agents.get(crow_id)

    def clear_agents(self) -> None:
        self._agents.clear()
        self.reset_patrol_state()

    def register_spawn(
        self,
        *,
        crow_id: str,
        role: str,
        sex: str,
        spawn_order: int,
        energy: float,
        curiosity: float,
        state: str,
    ) -> CrowAgent:
        agent = CrowAgent(
            crow_id=crow_id,
            home_roost_id=self.roost.id,
            energy=energy,
            curiosity=curiosity,
            state=state,
            role=role,
            sex=sex,
            spawn_order=spawn_order,
        )
        self._agents[crow_id] = agent
        return agent

    def ensure_agent(self, crow: CrowState, *, lead: bool) -> CrowAgent:
        if crow.id not in self._agents:
            self._agents[crow.id] = CrowAgent(
                crow_id=crow.id,
                home_roost_id=self.roost.id,
                energy=0.78,
                curiosity=0.55,
                state="FOLLOWING_PLAYER" if lead else "PATROLLING",
                role="lead" if lead else "wing",
                spawn_order=1 if lead else len(self._agents) + 1,
            )
        return self._agents[crow.id]

    def update(
        self,
        crows: list[CrowState],
        *,
        player_control: bool,
        player_tracking: bool,
        player_perch: bool,
        wall_ts: float | None = None,
        sim_dt: float = 0.033,
    ) -> None:
        ts = wall_ts if wall_ts is not None else time.time()
        self._last_sim_dt = sim_dt
        hour = resolve_cycle_hour(ts, self._cycle_cfg)
        self._cycle_hour = hour
        self._schedule_phase = schedule_phase(hour)
        self._legacy_phase = legacy_phase(self._schedule_phase)

        self._phase_note = PHASE_NOTES.get(
            self._schedule_phase, "Colony routine in progress."
        )

        if player_tracking and not self._greeted:
            self._greeted = True

        for i, crow in enumerate(crows):
            agent = self.ensure_agent(crow, lead=i == 0)
            dist_home = self.distance_to_roost(crow)

            if i == 0:
                if not player_control:
                    agent.state = self._auto_agent_state(dist_home)
                elif player_perch:
                    agent.state = "PERCHING"
                elif player_tracking:
                    agent.state = "FOLLOWING_PLAYER"
                elif self._legacy_phase == "evening_return" or dist_home > self.roost.territory_radius * 0.85:
                    agent.state = "RETURNING"
                elif self._legacy_phase == "night_roost":
                    agent.state = "PERCHING"
                else:
                    agent.state = "FORAGING"
            elif player_control and player_tracking and not player_perch:
                agent.state = "FOLLOWING_PLAYER"
            elif not player_control:
                agent.state = self._auto_agent_state(dist_home)
            elif self._legacy_phase == "evening_return" and dist_home > 12:
                agent.state = "RETURNING"
            elif self._legacy_phase == "morning_departure":
                agent.state = "FORAGING" if dist_home > 8 else "PERCHING"
            elif self._legacy_phase == "night_roost":
                agent.state = "PERCHING"
            elif dist_home > 10:
                agent.state = "PATROLLING"
            else:
                agent.state = "PERCHING"

            agent.energy = max(0.0, min(1.0, agent.energy - 0.001))
            if crow.mode == "flap":
                agent.energy = min(1.0, agent.energy + 0.004)
            agent.curiosity = min(1.0, agent.curiosity + 0.0005)

        self._update_colony_metrics(crows, player_control=player_control)

    def distance_to_roost(self, crow: CrowState) -> float:
        return math.hypot(crow.x - self.roost.x, crow.z - self.roost.z)

    def guidance_force(self, crow: CrowState, dt: float) -> tuple[float, float]:
        dx = self.roost.x - crow.x
        dz = self.roost.z - crow.z
        dist = math.hypot(dx, dz)
        radius = self.roost.territory_radius
        if dist < 1e-6:
            return 0.0, 0.0

        if self._schedule_phase in SCHEDULE_ROOST_PERCH:
            if dist > 1.5:
                strength = min(3.2, dist * 0.22) * dt
                return (dx / dist) * strength, (dz / dist) * strength
            return 0.0, 0.0

        if dist <= radius:
            return 0.0, 0.0
        overshoot = min(1.0, (dist - radius) / max(radius, 1e-6))
        strength = float(4.5 * overshoot * dt)
        if self._schedule_phase in SCHEDULE_RETURN:
            strength *= 1.35 + trait_scalar(self._trait_mods, "return_bias")
        return (dx / dist) * strength, (dz / dist) * strength

    def _flock_telemetry_slice(self, crows: list[CrowState]) -> list[dict[str, Any]]:
        return [
            {
                "id": c.id,
                "position": [c.x, c.y, c.z],
                "rotation": [c.pitch, c.yaw, c.roll],
            }
            for c in crows
        ]

    def _update_colony_metrics(
        self, crows: list[CrowState], *, player_control: bool
    ) -> None:
        agents_payload = [
            {
                "id": agent.crow_id,
                "state": agent.state,
                "distance_home": self.distance_to_roost(c),
            }
            for c in crows
            if (agent := self._agents.get(c.id)) is not None
        ]
        agent_states = [str(a["state"]) for a in agents_payload]
        flock_slice = self._flock_telemetry_slice(crows)
        territory = self.roost.territory_radius
        dispersion = flock_dispersion(flock_slice, territory)
        proximity = roost_proximity(agents_payload, territory)

        self._observed_behavior = infer_observed_behavior(
            agent_states,
            roost_proximity=proximity,
            dispersion=dispersion,
        )
        self._phase_deviation = phase_deviation(
            self._schedule_phase, self._observed_behavior
        )
        disturbance = self._observed_behavior == "disturbance_response" or (
            player_control and any(s == "FOLLOWING_PLAYER" for s in agent_states)
        )
        self._coherence = compute_coherence(
            agent_states=agent_states,
            flock=flock_slice,
            roost_proximity=proximity,
            dispersion=dispersion,
            trait_mods=self._trait_mods,
            schedule_phase_name=self._schedule_phase,
        )
        self._pressure = compute_pressure(
            flock_count=len(crows),
            population=self.roost.population,
            noise_level=self.roost.noise_level,
            food_score=self.roost.food_score,
            phase_deviation_score=self._phase_deviation,
            dispersion=dispersion,
            trait_mods=self._trait_mods,
            disturbance_active=disturbance,
        )
        self._call_density = estimate_call_density(
            self._schedule_phase, self._trait_mods
        )
        self._acoustic_pressure = acoustic_pressure(
            self._call_density, self.roost.noise_level
        )
        self._colony_events = colony_event_hints(
            schedule_phase=self._schedule_phase,
            coherence=self._coherence,
            pressure=self._pressure,
            phase_deviation_score=self._phase_deviation,
            observed_behavior=self._observed_behavior,
            disturbance_active=disturbance,
        )

    def spawn_offset(self, index: int, total: int) -> tuple[float, float, float]:
        spread = 2.2
        angle = (index / max(total, 1)) * math.pi * 2
        return (
            self.roost.x + math.sin(angle) * spread,
            self.roost.y + 3.5 + index * 0.4,
            self.roost.z + math.cos(angle) * spread + 6.0,
        )

    def to_telemetry(self, crows: list[CrowState]) -> dict[str, Any]:
        greeting = None
        if self._greeted and self.roost.pilot_trust >= 0.5:
            greeting = f"The roost recognizes {self.roost.pilot_name}."

        return {
            "roost": self.roost.to_dict(),
            "roosts": [r.to_dict() for r in self._roosts],
            "home_roost_id": self.roost.id,
            "daily_cycle": self._schedule_phase,
            "cycle_hour": round(self._cycle_hour, 2),
            "next_phase": next_phase(self._schedule_phase),
            "expected_phase": self._schedule_phase,
            "observed_behavior": self._observed_behavior,
            "phase_deviation": round(self._phase_deviation, 3),
            "coherence": round(self._coherence, 3),
            "pressure": round(self._pressure, 3),
            "call_density": round(self._call_density, 2),
            "acoustic_pressure": round(self._acoustic_pressure, 3),
            "legacy_daily_cycle": self._legacy_phase,
            "phase_note": self._phase_note,
            "events": self._colony_events,
            "greeting": greeting,
            "agents": [
                {
                    "id": agent.crow_id,
                    "home_roost": self.roost.id,
                    "energy": round(agent.energy, 3),
                    "curiosity": round(agent.curiosity, 3),
                    "state": agent.state,
                    "role": agent.role,
                    "sex": agent.sex,
                    "spawn_order": agent.spawn_order,
                    "distance_home": round(self.distance_to_roost(c), 2),
                }
                for c in crows
                if (agent := self._agents.get(c.id)) is not None
            ],
        }
