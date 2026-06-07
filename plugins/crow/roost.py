"""Roost + colony agent model — crows as population, not particles."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

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
    """Simple routines: morning departures, evening regroup, player as one agent."""

    def __init__(self, cfg: dict[str, Any]) -> None:
        loc = cfg.get("location", [0, 8, 0])
        geo = cfg.get("geo", {})
        self.roost = Roost(
            id=str(cfg.get("id", "north_yard")),
            name=str(cfg.get("name", "North Yard Roost")),
            x=float(loc[0]),
            y=float(loc[1]),
            z=float(loc[2]),
            population=int(cfg.get("population", 20)),
            food_score=float(cfg.get("food_score", 0.72)),
            safety_score=float(cfg.get("safety_score", 0.85)),
            noise_level=float(cfg.get("noise_level", 0.15)),
            territory_radius=float(cfg.get("territory_radius", 45)),
            pilot_name=str(cfg.get("pilot_name", "Pilot")),
            pilot_trust=float(cfg.get("pilot_trust", 0.85)),
            lat=geo.get("lat"),
            lon=geo.get("lon"),
        )
        self._agents: dict[str, CrowAgent] = {}
        self._greeted = False
        self._phase_note = "The roost is quiet."
        self._cycle_phase = "midday_forage"
        self._cycle_cfg = cfg.get("daily_cycle", {})
        self._waypoint_index = 0
        self._patrol_points = self._build_patrol_points()

    @property
    def cycle_phase(self) -> str:
        return self._cycle_phase

    def _build_patrol_points(self) -> list[tuple[float, float, float]]:
        bearings = self._cycle_cfg.get(
            "patrol_bearings", [0, 60, 120, 180, 240, 300]
        )
        radius_frac = float(self._cycle_cfg.get("patrol_radius_fraction", 0.52))
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

    def routine_target(self, crow: CrowState) -> tuple[float, float, float]:
        """Stable waypoint for autopilot — roost at night, patrol loop by day."""
        arrival = float(self._cycle_cfg.get("waypoint_arrival", 7.0))
        roost_y = self.roost.y + float(self._cycle_cfg.get("roost_perch_height", 2.0))
        approach_y = self.roost.y + float(self._cycle_cfg.get("roost_approach_height", 5.0))

        if self._cycle_phase == "night_roost":
            return (self.roost.x, roost_y, self.roost.z)

        if self._cycle_phase == "evening_return":
            return (self.roost.x, approach_y, self.roost.z + 5.0)

        target = self._patrol_point(self._waypoint_index)
        dist = math.hypot(crow.x - target[0], crow.z - target[2])
        # Advance only when genuinely arrived — prevents orbit/spin at waypoint gate.
        if dist < float(self._cycle_cfg.get("waypoint_arrival_inner", 5.0)):
            self._waypoint_index = (self._waypoint_index + 1) % max(
                len(self._patrol_points), 1
            )
            target = self._patrol_point(self._waypoint_index)
        return target

    def get_agent(self, crow_id: str) -> CrowAgent | None:
        return self._agents.get(crow_id)

    def clear_agents(self) -> None:
        self._agents.clear()

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
    ) -> None:
        ts = wall_ts if wall_ts is not None else time.time()
        hour = resolve_cycle_hour(ts, self._cycle_cfg)
        self._cycle_phase = daily_phase(hour)

        notes = {
            "morning_departure": "Morning departures — the murder scatters to scout.",
            "midday_forage": "Midday patrol — territory watch and foraging.",
            "evening_return": "Evening regroup — crows drift back toward the roost.",
            "night_roost": "Night roost — low movement, occupancy at the home tree.",
        }
        self._phase_note = notes[self._cycle_phase]

        if player_tracking and not self._greeted:
            self._greeted = True

        for i, crow in enumerate(crows):
            agent = self.ensure_agent(crow, lead=i == 0)
            dist_home = self.distance_to_roost(crow)

            if i == 0:
                if not player_control:
                    if self._cycle_phase == "night_roost":
                        agent.state = "PERCHING"
                    elif self._cycle_phase == "evening_return":
                        agent.state = "RETURNING"
                    elif self._cycle_phase == "morning_departure":
                        agent.state = "FORAGING"
                    else:
                        agent.state = "PATROLLING"
                elif player_perch:
                    agent.state = "PERCHING"
                elif player_tracking:
                    agent.state = "FOLLOWING_PLAYER"
                elif self._cycle_phase == "evening_return" or dist_home > self.roost.territory_radius * 0.85:
                    agent.state = "RETURNING"
                elif self._cycle_phase == "night_roost":
                    agent.state = "PERCHING"
                else:
                    agent.state = "FORAGING"
            elif player_control and player_tracking and not player_perch:
                agent.state = "FOLLOWING_PLAYER"
            elif not player_control:
                agent.state = (
                    "PERCHING"
                    if self._cycle_phase == "night_roost"
                    else "PATROLLING"
                )
            elif self._cycle_phase == "evening_return" and dist_home > 12:
                agent.state = "RETURNING"
            elif self._cycle_phase == "morning_departure":
                agent.state = "FORAGING" if dist_home > 8 else "PERCHING"
            elif self._cycle_phase == "night_roost":
                agent.state = "PERCHING"
            elif dist_home > 10:
                agent.state = "PATROLLING"
            else:
                agent.state = "PERCHING"

            agent.energy = max(0.0, min(1.0, agent.energy - 0.001))
            if crow.mode == "flap":
                agent.energy = min(1.0, agent.energy + 0.004)
            agent.curiosity = min(1.0, agent.curiosity + 0.0005)

    def distance_to_roost(self, crow: CrowState) -> float:
        return math.hypot(crow.x - self.roost.x, crow.z - self.roost.z)

    def guidance_force(self, crow: CrowState, dt: float) -> tuple[float, float]:
        dx = self.roost.x - crow.x
        dz = self.roost.z - crow.z
        dist = math.hypot(dx, dz)
        radius = self.roost.territory_radius
        if dist <= radius or dist < 1e-6:
            return 0.0, 0.0
        overshoot = min(1.0, (dist - radius) / max(radius, 1e-6))
        strength = float(4.5 * overshoot * dt)
        if self._cycle_phase == "evening_return":
            strength *= 1.35
        return (dx / dist) * strength, (dz / dist) * strength

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
            "daily_cycle": self._cycle_phase,
            "phase_note": self._phase_note,
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
