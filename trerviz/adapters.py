"""Plugin adapters — map native state into normalized VizPackets."""

from __future__ import annotations

import math
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from .schema import (
    METRIC_KEYS,
    PHASES,
    VizEvent,
    VizField,
    VizPacket,
    VIZ_SCHEMA_VERSION,
    clamp01,
    empty_metrics,
    merge_metrics,
)

def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_speed(velocity: list[float], ref: float = 12.0) -> float:
    if not velocity:
        return 0.0
    return clamp01(math.hypot(*velocity) / max(ref, 1e-6))


def _agents_by_id(agents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(a.get("id", "")): a for a in agents if a.get("id")}


# Flight-state stress weights for per-node field pressure.
_CROW_STATE_STRESS = {
    "ALARMED": 0.95,
    "RETURNING": 0.55,
    "FOLLOWING_PLAYER": 0.4,
    "FORAGING": 0.35,
    "PATROLLING": 0.22,
    "PERCHING": 0.12,
}


def _crow_flock_dispersion(
    birds: list[dict[str, Any]],
    territory_radius: float,
) -> float:
    """Spatial spread of the flock — 0 = tight, 1 = widely scattered."""
    if len(birds) < 2 or territory_radius <= 0:
        return 0.0
    positions = [
        (float((b.get("position") or [0, 0, 0])[0]), float((b.get("position") or [0, 0, 0])[2]))
        for b in birds
    ]
    cx = sum(p[0] for p in positions) / len(positions)
    cz = sum(p[1] for p in positions) / len(positions)
    mean_dist = sum(math.hypot(px - cx, pz - cz) for px, pz in positions) / len(positions)
    # ~18% of territory radius ≈ tight formation; ~55% ≈ scattered.
    return clamp01((mean_dist - territory_radius * 0.18) / max(territory_radius * 0.37, 1e-6))


def _crow_agent_conflict(states: list[str], daily_cycle: str) -> float:
    """Conflicting routines raise instability (e.g. RETURNING vs PATROLLING during regroup)."""
    unique = {s for s in states if s}
    if len(unique) <= 1:
        return 0.0
    score = clamp01((len(unique) - 1) / 3.0)
    if daily_cycle in ("evening_return", "night_roost"):
        if "RETURNING" in unique and ("PATROLLING" in unique or "FORAGING" in unique):
            score = max(score, 0.65)
        if "PERCHING" in unique and ("RETURNING" in unique or "FORAGING" in unique):
            score = max(score, 0.5)
    return score


def _crow_regroup_distance_pressure(
    agents: list[dict[str, Any]],
    territory_radius: float,
    daily_cycle: str,
) -> float:
    """Share of agents still far from roost during evening/night regroup."""
    if daily_cycle not in ("evening_return", "night_roost") or not agents:
        return 0.0
    threshold = territory_radius * 0.45
    far = sum(1 for a in agents if float(a.get("distance_home", 0.0)) > threshold)
    return clamp01(far / len(agents))


def _crow_roost_proximity(
    agents: list[dict[str, Any]],
    territory_radius: float,
) -> float:
    """Mean nearness to roost — 1 = clustered at home, 0 = spread to territory edge."""
    if not agents or territory_radius <= 0:
        return 0.5
    mean_dist = sum(float(a.get("distance_home", 0.0)) for a in agents) / len(agents)
    return clamp01(1.0 - mean_dist / territory_radius)


def _crow_glide_stability(
    glide: bool,
    stall: float,
    speed: float,
) -> float:
    """Steady glide with low stall = strong dissipation; chaotic glide = weak."""
    if not glide:
        return 0.0
    stall_ok = clamp01(1.0 - stall / 0.45)
    speed_ok = clamp01(1.0 - abs(speed - 0.32) / 0.32)
    return clamp01(stall_ok * 0.65 + speed_ok * 0.35)


def _crow_sentinel_roost_boost(agents: list[dict[str, Any]], territory: float) -> float:
    """Sentinels near roost increase anchor stability."""
    sentinels = [a for a in agents if str(a.get("role", "")) == "sentinel"]
    if not sentinels or territory <= 0:
        return 0.0
    near = sum(
        1 for a in sentinels if float(a.get("distance_home", 0.0)) < territory * 0.3
    )
    return clamp01(near / len(sentinels))


def _crow_node_pressure(
    *,
    distance_home: float,
    energy: float,
    state: str,
    stall: float,
    territory_radius: float,
    is_lead: bool,
    role: str = "",
) -> float:
    """Per-bird field pressure from home distance, energy, routine state, and flock stall."""
    dist_p = clamp01(distance_home / max(territory_radius, 1e-6))
    energy_p = clamp01(max(0.0, (0.42 - energy) / 0.42))
    state_p = _CROW_STATE_STRESS.get(state, 0.3)
    stall_w = 0.22 if is_lead else 0.12
    node_p = clamp01(dist_p * 0.38 + energy_p * 0.28 + state_p * 0.28 + stall * stall_w)
    if role == "scout":
        node_p = clamp01(node_p + 0.06)
    elif role == "juvenile":
        node_p = clamp01(node_p + 0.05)
    elif role == "sentinel" and dist_p < 0.35:
        node_p = clamp01(node_p - 0.08)
    elif role == "wing":
        node_p = clamp01(node_p - 0.03)
    # Wings stay low-pressure satellites; lead carries slightly more agent signal.
    return node_p if is_lead else clamp01(node_p * 0.82)


def _crow_roost_field_pressure(
    *,
    dispersion: float,
    evening_dist_p: float,
    noise: float,
    roost_proximity: float,
    criticality: float,
    healthy_convergence: bool,
    formation: str = "",
    sentinel_boost: float = 0.0,
) -> float:
    """Roost is the field anchor — dominant during stable regroup, not each bird."""
    base = clamp01(
        dispersion * 0.32
        + evening_dist_p * 0.22
        + noise * 0.18
        + (1.0 - roost_proximity) * 0.28
    )
    if healthy_convergence or (criticality < 0.28 and roost_proximity >= 0.45):
        base = max(base, clamp01(0.36 + roost_proximity * 0.12 + dispersion * 0.08))
    if formation == "roost_cluster":
        base = clamp01(base * 0.9 + 0.08)
    elif formation == "patrol_wedge":
        base = clamp01(base * 0.96 + 0.03)
    elif formation == "scout_line":
        base = clamp01(base * 1.05 + dispersion * 0.04)
    if sentinel_boost > 0:
        base = clamp01(base * (1.0 - sentinel_boost * 0.12) + sentinel_boost * 0.1)
    return base


def _crow_build_fields(
    *,
    roost: dict[str, Any],
    cycle: str,
    flock: list[dict[str, Any]],
    lead: dict[str, Any],
    agent_map: dict[str, dict[str, Any]],
    agents: list[dict[str, Any]],
    energy: float,
    stall: float,
    territory: float,
    dispersion: float,
    evening_dist_p: float,
    noise: float,
    roost_proximity: float,
    criticality: float,
    healthy_convergence: bool,
    formation: str = "",
) -> list[VizField]:
    """One roost anchor + one node per crow (deduped by id)."""
    roost_loc = roost.get("location") or [0.0, 8.0, 0.0]
    sentinel_boost = _crow_sentinel_roost_boost(agents, territory)
    roost_field_p = _crow_roost_field_pressure(
        dispersion=dispersion,
        evening_dist_p=evening_dist_p,
        noise=noise,
        roost_proximity=roost_proximity,
        criticality=criticality,
        healthy_convergence=healthy_convergence,
        formation=formation,
        sentinel_boost=sentinel_boost,
    )
    fields: list[VizField] = [
        VizField(
            id="roost",
            x=float(roost_loc[0]),
            y=float(roost_loc[2]),
            pressure=roost_field_p,
            radius=3.0 + roost_field_p * 9.0,
            label=f"{roost.get('name', 'Roost')} · anchor",
        )
    ]

    lead_pos = lead.get("position") or [0.0, 10.0, 0.0]
    seen: set[str] = set()

    for bird in flock:
        bird_id = str(bird.get("id", "crow"))
        if bird_id in seen:
            continue
        seen.add(bird_id)

        agent = agent_map.get(bird_id, {})
        if bird_id == "lead":
            pos = lead_pos
        else:
            pos = bird.get("position") or [0.0, 0.0, 0.0]

        state = str(agent.get("state") or bird.get("agent_state") or bird.get("mode", ""))
        role = str(agent.get("role") or bird.get("role") or "")
        node_p = _crow_node_pressure(
            distance_home=float(agent.get("distance_home", 0.0)),
            energy=float(agent.get("energy", energy)),
            state=state,
            stall=stall,
            territory_radius=territory,
            is_lead=bird_id == "lead",
            role=role,
        )
        fields.append(
            VizField(
                id=bird_id,
                x=float(pos[0]),
                y=float(pos[2]),
                pressure=node_p,
                radius=(2.2 + node_p * 5.0) if bird_id == "lead" else (1.5 + node_p * 3.0),
                label=(
                    f"{bird_id} · {role} · {state}"
                    if role and state
                    else f"{bird_id} · {state}"
                    if state
                    else bird_id
                ),
            )
        )

    return fields


_CYCLE_EXPECTED_STATES: dict[str, set[str]] = {
    "morning_departure": {"FORAGING", "RETURNING", "PATROLLING"},
    "midday_forage": {"PATROLLING", "FORAGING"},
    "evening_return": {"RETURNING", "PERCHING"},
    "night_roost": {"PERCHING"},
}


def _extract_call_density(raw: dict[str, Any]) -> float:
    """Calls/min from future audio listener or colony export; 0.0 when absent."""
    candidates = [
        (raw.get("audio") or {}).get("call_density"),
        (raw.get("colony") or {}).get("call_density"),
        ((raw.get("colony") or {}).get("roost") or {}).get("call_density"),
        (raw.get("metrics") or {}).get("call_density"),
    ]
    for value in candidates:
        if value is not None:
            return max(0.0, float(value))
    return 0.0


def _crow_state_uniformity(states: list[str]) -> float:
    """Share of agents in the dominant routine state."""
    clean = [s for s in states if s]
    if not clean:
        return 0.5
    counts: dict[str, int] = {}
    for state in clean:
        counts[state] = counts.get(state, 0) + 1
    dominant = max(counts.values())
    return dominant / len(clean)


def _crow_heading_alignment(flock: list[dict[str, Any]]) -> float:
    """1.0 = aligned headings, 0.0 = random scatter (circular mean resultant)."""
    yaws = [
        float((bird.get("rotation") or [0, 0, 0])[1])
        for bird in flock
        if bird.get("rotation") is not None or bird.get("id")
    ]
    if len(yaws) < 2:
        return 1.0
    mean_sin = sum(math.sin(y) for y in yaws) / len(yaws)
    mean_cos = sum(math.cos(y) for y in yaws) / len(yaws)
    return clamp01(math.hypot(mean_sin, mean_cos))


def _crow_cycle_alignment(states: list[str], daily_cycle: str) -> float:
    """Fraction of agents in states appropriate for the daily_cycle phase."""
    expected = _CYCLE_EXPECTED_STATES.get(daily_cycle)
    clean = [s for s in states if s]
    if not expected or not clean:
        return 0.5
    matched = sum(1 for state in clean if state in expected)
    return matched / len(clean)


def _crow_role_coherence_shift(agents: list[dict[str, Any]]) -> float:
    """Role mix nudges coherence — wing/sentinel help, scout/juvenile scatter."""
    shift = 0.0
    for agent in agents:
        role = str(agent.get("role", ""))
        if role == "wing":
            shift += 0.03
        elif role == "sentinel":
            shift += 0.025
        elif role == "lead":
            shift += 0.02
        elif role == "scout":
            shift -= 0.04
        elif role == "juvenile":
            shift -= 0.05
    return max(-0.15, min(0.12, shift))


def _crow_coherence(
    *,
    dispersion: float,
    agent_states: list[str],
    agents: list[dict[str, Any]],
    flock: list[dict[str, Any]],
    daily_cycle: str,
    stall: float,
    noise: float,
    conflict: float,
    regroup_far: float,
    roost_proximity: float,
    healthy_convergence: bool,
    formation: str = "",
) -> float:
    """Colony-level alignment: space + state + heading + routine + calm."""
    proximity = 1.0 - dispersion
    state_match = _crow_state_uniformity(agent_states)
    heading = _crow_heading_alignment(flock)
    routine = _crow_cycle_alignment(agent_states, daily_cycle)
    calm = clamp01((1.0 - stall) * 0.55 + (1.0 - noise) * 0.45)

    # Phase bonuses: regrouping at roost or tight midday patrol.
    phase_bonus = 0.0
    if daily_cycle == "evening_return":
        phase_bonus = roost_proximity * 0.12
    elif daily_cycle == "midday_forage":
        phase_bonus = proximity * 0.08
    if healthy_convergence:
        phase_bonus = max(phase_bonus, 0.10)

    coherence = clamp01(
        proximity * 0.28
        + state_match * 0.24
        + heading * 0.18
        + routine * 0.14
        + calm * 0.10
        + phase_bonus
    )
    # Fragmentation penalties + role/formation readiness.
    coherence = clamp01(coherence - conflict * 0.18 - regroup_far * 0.14)
    coherence = clamp01(coherence + _crow_role_coherence_shift(agents))
    if formation == "roost_cluster":
        coherence = clamp01(coherence + 0.04)
    elif formation == "patrol_wedge":
        coherence = clamp01(coherence + 0.02)
    return coherence


def _crow_colony_events(
    events: list[VizEvent],
    *,
    coherence: float,
    call_density: float,
) -> list[VizEvent]:
    """Coherence and call-density readiness signals."""
    if coherence > 0.8:
        events.append(VizEvent("reorganization", "Colony coherence high", "low"))
    elif coherence < 0.4:
        events.append(VizEvent("pressure_spike", "Colony coherence fragmented", "medium"))
    if call_density > 60.0:
        events.append(VizEvent("pressure_spike", "Call density rising", "medium"))
    if call_density > 90.0 and coherence < 0.5:
        events.append(
            VizEvent(
                "rupture_warning",
                "Disturbance-like acoustic pressure",
                "high",
            )
        )
    return events


def _crow_is_healthy_convergence(
    *,
    daily_cycle: str,
    roost_proximity: float,
    stall: float,
    regroup_far: float,
    conflict: float,
    dispersion: float,
    criticality: float,
) -> bool:
    """Healthy evening regroup — target VizPacket profile:
    phase=reorganization, pressure low/moderate, criticality low,
    rupture_risk very low, dissipation high, main event=Roost convergence underway.
    """
    return (
        daily_cycle == "evening_return"
        and roost_proximity >= 0.5
        and stall < 0.35
        and regroup_far < 0.3
        and conflict < 0.4
        and dispersion < 0.42
        and criticality < 0.38
    )


def _crow_apply_convergence_calm(
    metrics: dict[str, float],
    *,
    dissipation: float,
) -> dict[str, float]:
    """Nudge metrics into the calm reorganization band when convergence is healthy."""
    return {
        **metrics,
        # Keep residual evening pressure visible but not alarming.
        "pressure": clamp01(min(metrics["pressure"], 0.45)),
        "criticality": clamp01(min(metrics["criticality"], 0.32)),
        "rupture_risk": clamp01(min(metrics["rupture_risk"], 0.12)),
        "dissipation": clamp01(max(dissipation, 0.58)),
    }


def _crow_trer_phase(
    *,
    daily_cycle: str,
    stall: float,
    pressure: float,
    criticality: float,
    noise: float,
    dispersion: float,
    glide: bool,
    roost_proximity: float,
    healthy_convergence: bool,
) -> str:
    """Map colony/flight signals into TRER phase (instability-aware, not activity-aware)."""
    if stall >= 0.68:
        return "overextension"
    if healthy_convergence or (
        daily_cycle == "evening_return"
        and roost_proximity >= 0.62
        and stall < 0.32
        and criticality < 0.4
        and (glide or roost_proximity >= 0.7)
    ):
        return "reorganization"
    if criticality >= 0.62 or (noise >= 0.55 and dispersion >= 0.48):
        return "peak"
    if noise >= 0.48 or dispersion >= 0.42 or criticality >= 0.45:
        return "surge"
    if daily_cycle == "night_roost" or (pressure < 0.28 and criticality < 0.3):
        return "refractory"
    if daily_cycle == "morning_departure" or (pressure < 0.38 and dispersion < 0.35):
        return "build_up"
    if daily_cycle == "evening_return":
        return "discharge"
    return "surge" if pressure >= 0.45 else "build_up"


class BaseAdapter(ABC):
    plugin: str

    @abstractmethod
    def adapt(self, raw: dict[str, Any], *, source: str) -> VizPacket:
        raise NotImplementedError

    def demo(self, *, t: float | None = None) -> VizPacket:
        raise NotImplementedError(f"{self.plugin} demo not implemented")


class CrowAdapter(BaseAdapter):
    plugin = "crow"

    def adapt(self, raw: dict[str, Any], *, source: str) -> VizPacket:
        meta = raw.get("meta") or {}
        status = raw.get("status") or {}
        lead = raw.get("lead") or {}
        colony = raw.get("colony") or {}
        controls = raw.get("controls") or {}
        flock = raw.get("flock") or []
        agents = colony.get("agents") or []
        agent_map = _agents_by_id(agents)

        energy = float(status.get("energy", 0.0))
        stall = float(status.get("stall_risk", 0.0))
        glide = bool(controls.get("glide", False))
        velocity = lead.get("velocity") or [0.0, 0.0, 0.0]
        speed = _norm_speed(velocity)

        roost = colony.get("roost") or {}
        cycle = str(colony.get("daily_cycle", ""))
        territory = float(roost.get("territory_radius", 45.0))
        noise = float(roost.get("noise_level", 0.15))
        safety = float(roost.get("safety_score", 0.85))
        food = float(roost.get("food_score", 0.72))

        dispersion = _crow_flock_dispersion(flock, territory)
        low_energy_p = clamp01(max(0.0, (0.45 - energy) / 0.45))
        lead_home = float((agent_map.get("lead") or {}).get("distance_home", 0.0))
        evening_dist_p = (
            clamp01(lead_home / max(territory * 0.55, 1e-6))
            if cycle == "evening_return"
            else 0.0
        )

        # Pressure: environmental + energetic stress, not raw activity.
        pressure = clamp01(
            stall * 0.30
            + noise * 0.20
            + low_energy_p * 0.15
            + evening_dist_p * 0.20
            + dispersion * 0.15
        )

        agent_states = [str(a.get("state", "")) for a in agents]
        conflict = _crow_agent_conflict(agent_states, cycle)
        regroup_far = _crow_regroup_distance_pressure(agents, territory, cycle)
        unsafe = clamp01(1.0 - safety)

        # Criticality: instability — stall, noise, weak safety, scatter, routine conflict.
        criticality = clamp01(
            stall * 0.28
            + noise * 0.18
            + unsafe * 0.18
            + dispersion * 0.18
            + conflict * 0.10
            + regroup_far * 0.18
        )

        roost_proximity = _crow_roost_proximity(agents, territory)
        glide_stable = _crow_glide_stability(glide, stall, speed)

        # Dissipation: roost capacity to absorb stress (safety, food, nearness, calm glide).
        dissipation = clamp01(
            safety * 0.28
            + food * 0.22
            + roost_proximity * 0.22
            + glide_stable * 0.18
            + (1.0 - noise) * 0.10
        )

        # Rupture: only when multiple stressors stack (not a single noisy signal).
        stress_flags = [
            stall >= 0.42,
            noise >= 0.48,
            unsafe >= 0.22,
            dispersion >= 0.45,
            low_energy_p >= 0.35,
            regroup_far >= 0.4,
        ]
        stacked = sum(1 for flag in stress_flags if flag)
        rupture_core = max(stall, noise, dispersion, unsafe)
        rupture_risk = (
            clamp01(((stacked - 2) / 3.0) * rupture_core) if stacked >= 3 else clamp01(0.12 * rupture_core)
        )

        healthy_convergence = _crow_is_healthy_convergence(
            daily_cycle=cycle,
            roost_proximity=roost_proximity,
            stall=stall,
            regroup_far=regroup_far,
            conflict=conflict,
            dispersion=dispersion,
            criticality=criticality,
        )

        launch_meta = raw.get("meta", {}).get("launch") or {}
        formation = str(launch_meta.get("formation", ""))

        coherence = _crow_coherence(
            dispersion=dispersion,
            agent_states=agent_states,
            agents=agents,
            flock=flock,
            daily_cycle=cycle,
            stall=stall,
            noise=noise,
            conflict=conflict,
            regroup_far=regroup_far,
            roost_proximity=roost_proximity,
            healthy_convergence=healthy_convergence,
            formation=formation,
        )
        call_density = _extract_call_density(raw)

        metrics = merge_metrics(
            {
                "pressure": pressure,
                "criticality": criticality,
                "velocity": speed,
                "acceleration": 0.0,
                "dissipation": dissipation,
                "rupture_risk": rupture_risk,
            },
            coherence=coherence,
            call_density=call_density,
        )
        if healthy_convergence:
            metrics = _crow_apply_convergence_calm(metrics, dissipation=dissipation)

        phase = _crow_trer_phase(
            daily_cycle=cycle,
            stall=stall,
            pressure=metrics["pressure"],
            criticality=metrics["criticality"],
            noise=noise,
            dispersion=dispersion,
            glide=glide,
            roost_proximity=roost_proximity,
            healthy_convergence=healthy_convergence,
        )

        fields = _crow_build_fields(
            roost=roost,
            cycle=cycle,
            flock=flock,
            lead=lead,
            agent_map=agent_map,
            agents=agents,
            energy=energy,
            stall=stall,
            territory=territory,
            dispersion=dispersion,
            evening_dist_p=evening_dist_p,
            noise=noise,
            roost_proximity=roost_proximity,
            criticality=metrics["criticality"],
            healthy_convergence=healthy_convergence,
            formation=formation,
        )

        events: list[VizEvent] = []
        if healthy_convergence:
            # Calm regroup: one clear headline, suppress noisy secondary warnings.
            events.append(
                VizEvent("reorganization", "Roost convergence underway", "low")
            )
        else:
            if cycle == "evening_return" and roost_proximity >= 0.5:
                events.append(
                    VizEvent("reorganization", "Roost convergence underway", "low")
                )
            if glide and energy < 0.35:
                events.append(
                    VizEvent("pressure_spike", "Low energy glide state", "medium")
                )
            if dispersion < 0.28 and metrics["criticality"] < 0.32 and stall < 0.35:
                events.append(VizEvent("reorganization", "Flock stable", "low"))
            if stall >= 0.42:
                sev = "high" if stall >= 0.68 else "medium"
                events.append(
                    VizEvent("rupture_warning", "Elevated stall risk", sev)
                )
            if noise >= 0.5:
                events.append(
                    VizEvent("pressure_spike", "Noise pressure rising", "medium")
                )
            if regroup_far >= 0.4:
                events.append(
                    VizEvent(
                        "rupture_warning",
                        "Agent far from roost during evening return",
                        "high" if regroup_far >= 0.65 else "medium",
                    )
                )
            if metrics["rupture_risk"] >= 0.55:
                events.append(
                    VizEvent(
                        "rupture_warning",
                        "Stacked stressors — rupture risk elevated",
                        "high",
                    )
                )

        events = _crow_colony_events(
            events,
            coherence=metrics["coherence"],
            call_density=metrics["call_density"],
        )

        return VizPacket(
            schema_version=VIZ_SCHEMA_VERSION,
            plugin=self.plugin,
            timestamp=_iso_now(),
            phase=phase,
            metrics=metrics,
            fields=fields,
            events=events[-8:],
            source=source,
            raw=raw,
        )

    def demo(self, *, t: float | None = None) -> VizPacket:
        t = time.time() if t is None else t
        phase_idx = int(t / 8.0) % len(PHASES)
        phase = PHASES[phase_idx]
        pressure = clamp01(0.35 + 0.25 * math.sin(t * 0.4))
        return VizPacket(
            schema_version=VIZ_SCHEMA_VERSION,
            plugin=self.plugin,
            timestamp=_iso_now(),
            phase=phase,
            metrics=merge_metrics(
                {
                    "pressure": pressure,
                    "criticality": clamp01(pressure * 0.8),
                    "velocity": clamp01(0.4 + 0.2 * math.sin(t * 0.2)),
                    "acceleration": 0.0,
                    "dissipation": clamp01(0.6 + 0.1 * math.cos(t * 0.3)),
                    "rupture_risk": clamp01(pressure * 0.5),
                },
                coherence=clamp01(0.55 + 0.25 * math.sin(t * 0.12)),
                call_density=0.0,
            ),
            fields=[
                VizField("roost", 0, 0, 0.42, 7.0, "Demo Roost · anchor"),
                VizField(
                    "lead",
                    12 * math.sin(t * 0.15),
                    12 * math.cos(t * 0.15),
                    pressure * 0.35,
                    2.5,
                    "lead · PATROLLING",
                ),
            ],
            events=[
                VizEvent("reorganization", "Demo crow patrol loop", "low"),
            ],
            source="demo",
        )


class InfrastructureAdapter(BaseAdapter):
    plugin = "infrastructure"

    def adapt(self, raw: dict[str, Any], *, source: str) -> VizPacket:
        metrics_raw = raw.get("metrics") or raw
        nodes = raw.get("nodes") or raw.get("fields") or []
        phase = str(raw.get("phase", "build_up"))

        metrics = empty_metrics()
        for key in METRIC_KEYS:
            if key in metrics_raw:
                value = float(metrics_raw[key])
                metrics[key] = value if key == "call_density" else clamp01(value)

        fields = []
        for node in nodes:
            if isinstance(node, dict) and "x" in node:
                fields.append(
                    VizField(
                        id=str(node.get("id", "node")),
                        x=float(node.get("x", 0.0)),
                        y=float(node.get("y", 0.0)),
                        pressure=clamp01(float(node.get("pressure", node.get("rupture_risk", 0.0)))),
                        radius=float(node.get("radius", 2.0)),
                        label=str(node.get("label", node.get("asset", ""))),
                    )
                )

        events = [
            VizEvent(
                type=str(item.get("type", "info")),
                message=str(item.get("message", "")),
                severity=str(item.get("severity", "low")),
            )
            for item in raw.get("events", [])
        ]

        return VizPacket(
            schema_version=VIZ_SCHEMA_VERSION,
            plugin=self.plugin,
            timestamp=str(raw.get("timestamp", _iso_now())),
            phase=phase if phase in PHASES else "build_up",
            metrics=metrics,
            fields=fields,
            events=events[-8:],
            source=source,
            raw=raw,
        )

    def demo(self, *, t: float | None = None) -> VizPacket:
        t = time.time() if t is None else t
        nodes = []
        for i, (nx, ny, label) in enumerate(
            [
                (0, 0, "Core"),
                (8, 4, "Grid-A"),
                (-6, 7, "Grid-B"),
                (5, -8, "Feeder"),
                (-9, -5, "Switch"),
            ]
        ):
            p = clamp01(0.3 + 0.35 * math.sin(t * 0.25 + i))
            nodes.append(VizField(f"asset_{i}", nx, ny, p, 2.0 + p * 3, label))

        rupture = clamp01(0.2 + 0.45 * math.sin(t * 0.18 + 1.2))
        events = []
        if rupture > 0.65:
            events.append(
                VizEvent("rupture_warning", "Dependency cascade risk on Grid-B", "high")
            )
        elif rupture > 0.45:
            events.append(
                VizEvent("pressure_spike", "Consequence density rising near Feeder", "medium")
            )

        return VizPacket(
            schema_version=VIZ_SCHEMA_VERSION,
            plugin=self.plugin,
            timestamp=_iso_now(),
            phase=PHASES[int(t / 10.0) % len(PHASES)],
            metrics=merge_metrics(
                {
                    "pressure": clamp01(0.4 + 0.2 * math.sin(t * 0.22)),
                    "criticality": clamp01(0.35 + 0.25 * math.cos(t * 0.17)),
                    "velocity": clamp01(0.15 + 0.1 * math.sin(t * 0.31)),
                    "acceleration": clamp01(0.05 + 0.05 * math.cos(t * 0.5)),
                    "dissipation": clamp01(0.55 - rupture * 0.2),
                    "rupture_risk": rupture,
                }
            ),
            fields=nodes,
            events=events,
            source="demo",
        )


class MarketAdapter(BaseAdapter):
    plugin = "market"

    def adapt(self, raw: dict[str, Any], *, source: str) -> VizPacket:
        infra = InfrastructureAdapter()
        packet = infra.adapt(raw, source=source)
        packet.plugin = self.plugin
        return packet

    def demo(self, *, t: float | None = None) -> VizPacket:
        t = time.time() if t is None else t
        compression = clamp01(0.5 + 0.4 * math.sin(t * 0.35))
        reflexive = clamp01(0.45 + 0.35 * math.cos(t * 0.27))
        phase = "overextension" if compression > 0.75 else "surge" if compression > 0.5 else "build_up"

        fields = [
            VizField("spot", 0, 0, compression, 4.0, "Spot"),
            VizField("vol", 10, 0, reflexive, 3.0, "Vol surface"),
            VizField("flow", -8, 6, clamp01(compression * reflexive), 2.5, "Flow"),
        ]
        events = []
        if compression > 0.72:
            events.append(
                VizEvent("pressure_spike", "Volatility compression — reflexive amplification", "high")
            )
        if reflexive > 0.7:
            events.append(
                VizEvent("discharge", "Decompression wave likely", "medium")
            )

        return VizPacket(
            schema_version=VIZ_SCHEMA_VERSION,
            plugin=self.plugin,
            timestamp=_iso_now(),
            phase=phase,
            metrics=merge_metrics(
                {
                    "pressure": compression,
                    "criticality": clamp01(compression * 0.55 + reflexive * 0.45),
                    "velocity": clamp01(0.2 + 0.25 * math.sin(t * 0.4)),
                    "acceleration": clamp01(0.1 + 0.15 * math.cos(t * 0.55)),
                    "dissipation": clamp01(1.0 - compression * 0.6),
                    "rupture_risk": clamp01(reflexive * 0.7),
                }
            ),
            fields=fields,
            events=events,
            source="demo",
        )


ADAPTERS: dict[str, BaseAdapter] = {
    "crow": CrowAdapter(),
    "infrastructure": InfrastructureAdapter(),
    "market": MarketAdapter(),
}


def adapt_packet(plugin: str, raw: dict[str, Any], *, source: str) -> VizPacket:
    """Route raw state through the plugin adapter."""
    if (
        int(raw.get("schema_version", 0)) == VIZ_SCHEMA_VERSION
        and str(raw.get("plugin", "")) == plugin
        and isinstance(raw.get("metrics"), dict)
    ):
        normalized = VizPacket.from_dict(raw)
        normalized.source = source
        return normalized

    adapter = ADAPTERS.get(plugin)
    if adapter is None:
        return VizPacket(
            schema_version=VIZ_SCHEMA_VERSION,
            plugin=plugin,
            timestamp=_iso_now(),
            phase="reorganization",
            metrics=empty_metrics(),
            source=source,
            raw=raw,
        )
    return adapter.adapt(raw, source=source)


def demo_packet(plugin: str) -> VizPacket:
    adapter = ADAPTERS.get(plugin)
    if adapter is None:
        return VizPacket(
            schema_version=VIZ_SCHEMA_VERSION,
            plugin=plugin,
            timestamp=_iso_now(),
            phase="build_up",
            metrics=empty_metrics(),
            source="demo",
        )
    return adapter.demo()
