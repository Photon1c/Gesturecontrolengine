"""Colony schedule, roost traits, coherence, pressure — roost-level simulation layer."""

from __future__ import annotations

import math
from typing import Any

# Time-based daily schedule (hour 0–24, works with compressed_cycle_minutes).
SCHEDULE_ROOST_PERCH = frozenset(
    {"sleep", "awakening", "settling", "social_chatter"}
)
SCHEDULE_RETURN = frozenset({"roost_convergence", "gathering_calls"})
SCHEDULE_NEAR_ROOST = frozenset({"morning_calls"})
SCHEDULE_OUTBOUND = frozenset({"dispersal"})
SCHEDULE_PATROL = frozenset({"foraging", "patrol"})

SCHEDULE_PHASES: tuple[str, ...] = (
    "sleep",
    "awakening",
    "morning_calls",
    "dispersal",
    "foraging",
    "patrol",
    "gathering_calls",
    "roost_convergence",
    "social_chatter",
    "settling",
)

# Hour boundaries: (start_hour, phase_name). Last segment wraps to sleep.
_SCHEDULE_BOUNDARIES: tuple[tuple[float, str], ...] = (
    (0.0, "sleep"),
    (4.0, "awakening"),
    (5.0, "morning_calls"),
    (6.0, "dispersal"),
    (8.0, "foraging"),
    (12.0, "patrol"),
    (17.0, "gathering_calls"),
    (18.0, "roost_convergence"),
    (20.0, "social_chatter"),
    (21.0, "settling"),
    (23.0, "sleep"),
)

# Maps new schedule phases → legacy 4-phase model (physics routines unchanged).
LEGACY_PHASE_MAP: dict[str, str] = {
    "sleep": "night_roost",
    "awakening": "night_roost",
    "morning_calls": "morning_departure",
    "dispersal": "morning_departure",
    "foraging": "midday_forage",
    "patrol": "midday_forage",
    "gathering_calls": "evening_return",
    "roost_convergence": "evening_return",
    "social_chatter": "night_roost",
    "settling": "night_roost",
}

ROOST_TRAITS: tuple[str, ...] = (
    "conservative",
    "aggressive",
    "exploratory",
    "social",
    "vigilant",
    "stable",
)

# Lightweight scalar modifiers per trait (summed then clamped).
_TRAIT_MODIFIERS: dict[str, dict[str, float]] = {
    "conservative": {
        "patrol_radius": -0.12,
        "call_frequency": -0.15,
        "convergence_timing": 0.10,
        "coherence": 0.05,
        "disturbance_sensitivity": 0.12,
        "return_bias": 0.08,
    },
    "aggressive": {
        "patrol_radius": 0.14,
        "call_frequency": 0.18,
        "convergence_timing": -0.08,
        "coherence": -0.04,
        "disturbance_sensitivity": -0.10,
        "return_bias": -0.06,
    },
    "exploratory": {
        "patrol_radius": 0.18,
        "call_frequency": 0.06,
        "convergence_timing": -0.12,
        "coherence": -0.06,
        "disturbance_sensitivity": -0.05,
        "return_bias": -0.10,
    },
    "social": {
        "patrol_radius": 0.04,
        "call_frequency": 0.22,
        "convergence_timing": 0.04,
        "coherence": 0.08,
        "disturbance_sensitivity": 0.02,
        "return_bias": 0.02,
    },
    "vigilant": {
        "patrol_radius": -0.06,
        "call_frequency": 0.12,
        "convergence_timing": 0.06,
        "coherence": 0.03,
        "disturbance_sensitivity": 0.18,
        "return_bias": 0.14,
    },
    "stable": {
        "patrol_radius": -0.04,
        "call_frequency": -0.04,
        "convergence_timing": 0.08,
        "coherence": 0.10,
        "disturbance_sensitivity": -0.12,
        "return_bias": 0.06,
    },
}

# Expected observed behavior labels per schedule phase.
_EXPECTED_BEHAVIORS: dict[str, set[str]] = {
    "sleep": {"roosting", "settling"},
    "awakening": {"roosting", "calling"},
    "morning_calls": {"calling", "dispersing"},
    "dispersal": {"dispersing", "foraging"},
    "foraging": {"foraging", "patrolling"},
    "patrol": {"patrolling", "foraging"},
    "gathering_calls": {"returning", "calling"},
    "roost_convergence": {"returning", "roosting"},
    "social_chatter": {"socializing", "roosting"},
    "settling": {"settling", "roosting"},
}

PHASE_NOTES: dict[str, str] = {
    "sleep": "Night roost — colony at rest on the home tree.",
    "awakening": "Awakening — first shifts on the branches.",
    "morning_calls": "Morning calls — contact calls before dispersal.",
    "dispersal": "Dispersal — scouts leave the roost perimeter.",
    "foraging": "Foraging — territory feeding runs.",
    "patrol": "Patrol — midday watch over the territory.",
    "gathering_calls": "Gathering calls — pre-convergence signaling.",
    "roost_convergence": "Roost convergence — birds stream back home.",
    "social_chatter": "Social chatter — brief roost-side interaction.",
    "settling": "Settling — occupancy tightens for the night.",
}

# Base call rate (calls/min) by schedule phase before trait modifiers.
_PHASE_CALL_BASE: dict[str, float] = {
    "sleep": 2.0,
    "awakening": 12.0,
    "morning_calls": 28.0,
    "dispersal": 14.0,
    "foraging": 8.0,
    "patrol": 6.0,
    "gathering_calls": 22.0,
    "roost_convergence": 16.0,
    "social_chatter": 20.0,
    "settling": 5.0,
}


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def schedule_phase(hour: float) -> str:
    """Return schedule phase for simulated hour 0–24."""
    h = hour % 24.0
    phase = _SCHEDULE_BOUNDARIES[0][1]
    for start, name in _SCHEDULE_BOUNDARIES:
        if h >= start:
            phase = name
    return phase


def next_phase(phase: str) -> str:
    """Next phase in the daily loop."""
    if phase not in SCHEDULE_PHASES:
        return SCHEDULE_PHASES[0]
    idx = SCHEDULE_PHASES.index(phase)
    return SCHEDULE_PHASES[(idx + 1) % len(SCHEDULE_PHASES)]


def legacy_phase(schedule: str) -> str:
    return LEGACY_PHASE_MAP.get(schedule, "midday_forage")


def combine_trait_modifiers(traits: list[str]) -> dict[str, float]:
    """Sum trait scalars; used as multiplicative offsets around 1.0."""
    keys = (
        "patrol_radius",
        "call_frequency",
        "convergence_timing",
        "coherence",
        "disturbance_sensitivity",
        "return_bias",
    )
    totals = {k: 0.0 for k in keys}
    for trait in traits:
        mods = _TRAIT_MODIFIERS.get(trait, {})
        for key in keys:
            totals[key] += float(mods.get(key, 0.0))
    return totals


def trait_scalar(modifiers: dict[str, float], key: str, default: float = 0.0) -> float:
    return float(modifiers.get(key, default))


def infer_observed_behavior(
    agent_states: list[str],
    *,
    roost_proximity: float,
    dispersion: float,
) -> str:
    """Colony-level behavior label from agent states + spatial signals."""
    clean = [s for s in agent_states if s]
    if not clean:
        return "roosting"
    n = len(clean)
    perch = sum(1 for s in clean if s == "PERCHING") / n
    returning = sum(1 for s in clean if s == "RETURNING") / n
    patrolling = sum(1 for s in clean if s == "PATROLLING") / n
    foraging = sum(1 for s in clean if s == "FORAGING") / n
    alarmed = sum(1 for s in clean if s == "ALARMED") / n

    if alarmed >= 0.25:
        return "disturbance_response"
    if returning >= 0.35:
        return "returning"
    if perch >= 0.6 and roost_proximity >= 0.55:
        return "roosting"
    if foraging >= 0.35:
        return "foraging"
    if patrolling >= 0.35 and dispersion >= 0.28:
        return "patrolling"
    if patrolling >= 0.3 or dispersion >= 0.32:
        return "dispersing"
    if perch >= 0.45 and roost_proximity >= 0.45:
        return "socializing"
    if perch >= 0.4:
        return "settling"
    return "patrolling"


def phase_deviation(expected_phase: str, observed_behavior: str) -> float:
    """0 = on schedule, 1 = strongly off schedule."""
    allowed = _EXPECTED_BEHAVIORS.get(expected_phase, {observed_behavior})
    if observed_behavior in allowed:
        return 0.0
    # Partial overlaps soften deviation.
    soft_matches: dict[str, dict[str, float]] = {
        "dispersal": {"patrolling": 0.35, "foraging": 0.2, "roosting": 0.85},
        "roost_convergence": {"patrolling": 0.55, "dispersing": 0.65, "foraging": 0.5},
        "morning_calls": {"roosting": 0.45, "patrolling": 0.4},
        "patrol": {"roosting": 0.75, "returning": 0.5},
        "sleep": {"patrolling": 0.9, "dispersing": 0.95, "returning": 0.7},
        "settling": {"patrolling": 0.6, "dispersing": 0.7},
    }
    return _clamp01(soft_matches.get(expected_phase, {}).get(observed_behavior, 0.72))


def state_uniformity(agent_states: list[str]) -> float:
    clean = [s for s in agent_states if s]
    if not clean:
        return 0.5
    dominant = max(set(clean), key=clean.count)
    return sum(1 for s in clean if s == dominant) / len(clean)


def heading_alignment(flock: list[dict[str, Any]]) -> float:
    yaws: list[float] = []
    for bird in flock:
        rot = bird.get("rotation") or [0.0, 0.0, 0.0]
        if len(rot) >= 2:
            yaws.append(float(rot[1]))
    if len(yaws) < 2:
        return 0.75
    mean_sin = sum(math.sin(y) for y in yaws) / len(yaws)
    mean_cos = sum(math.cos(y) for y in yaws) / len(yaws)
    resultant = math.hypot(mean_sin, mean_cos)
    return _clamp01(resultant)


def compute_coherence(
    *,
    agent_states: list[str],
    flock: list[dict[str, Any]],
    roost_proximity: float,
    dispersion: float,
    trait_mods: dict[str, float],
    schedule_phase_name: str,
) -> float:
    """Colony-level coherence 0–1."""
    proximity = 1.0 - dispersion
    state_match = state_uniformity(agent_states)
    heading = heading_alignment(flock)
    trait_boost = trait_scalar(trait_mods, "coherence")

    phase_bonus = 0.0
    if schedule_phase_name in ("roost_convergence", "settling", "sleep"):
        phase_bonus = roost_proximity * 0.10
    elif schedule_phase_name == "patrol":
        phase_bonus = proximity * 0.06

    coherence = _clamp01(
        proximity * 0.26
        + state_match * 0.28
        + heading * 0.18
        + roost_proximity * 0.12
        + phase_bonus
        + trait_boost
    )
    conflict = _agent_conflict(agent_states, schedule_phase_name)
    coherence = _clamp01(coherence - conflict * 0.16 - dispersion * 0.08)
    return coherence


def compute_pressure(
    *,
    flock_count: int,
    population: int,
    noise_level: float,
    food_score: float,
    phase_deviation_score: float,
    dispersion: float,
    trait_mods: dict[str, float],
    disturbance_active: bool,
) -> float:
    """Colony/roost pressure 0–1."""
    density = _clamp01(flock_count / max(population, 1))
    low_food = _clamp01(max(0.0, (0.55 - food_score) / 0.55))
    disturb = 0.22 if disturbance_active else 0.0
    disturb += trait_scalar(trait_mods, "disturbance_sensitivity") * 0.12

    return _clamp01(
        density * 0.18
        + noise_level * 0.22
        + phase_deviation_score * 0.24
        + low_food * 0.14
        + dispersion * 0.14
        + disturb
    )


def estimate_call_density(schedule_phase_name: str, trait_mods: dict[str, float]) -> float:
    """Placeholder calls/min until audio listener is wired."""
    base = _PHASE_CALL_BASE.get(schedule_phase_name, 6.0)
    mod = 1.0 + trait_scalar(trait_mods, "call_frequency")
    return max(0.0, base * mod)


def acoustic_pressure(call_density: float, noise_level: float) -> float:
    """Future mic hook — normalized acoustic load on roost."""
    return _clamp01(call_density / 40.0 * 0.55 + noise_level * 0.45)


def flock_dispersion(flock: list[dict[str, Any]], territory_radius: float) -> float:
    if len(flock) < 2 or territory_radius <= 0:
        return 0.0
    positions = [
        (float((b.get("position") or [0, 0, 0])[0]), float((b.get("position") or [0, 0, 0])[2]))
        for b in flock
    ]
    cx = sum(p[0] for p in positions) / len(positions)
    cz = sum(p[1] for p in positions) / len(positions)
    mean_dist = sum(math.hypot(px - cx, pz - cz) for px, pz in positions) / len(positions)
    return _clamp01((mean_dist - territory_radius * 0.18) / max(territory_radius * 0.37, 1e-6))


def roost_proximity(agents: list[dict[str, Any]], territory_radius: float) -> float:
    if not agents or territory_radius <= 0:
        return 0.5
    mean_dist = sum(float(a.get("distance_home", 0.0)) for a in agents) / len(agents)
    return _clamp01(1.0 - mean_dist / territory_radius)


def _agent_conflict(states: list[str], schedule_phase: str) -> float:
    unique = {s for s in states if s}
    if len(unique) <= 1:
        return 0.0
    score = _clamp01((len(unique) - 1) / 3.0)
    if schedule_phase in ("roost_convergence", "settling", "gathering_calls", "evening_return"):
        if "RETURNING" in unique and ("PATROLLING" in unique or "FORAGING" in unique):
            score = max(score, 0.65)
        if "PERCHING" in unique and ("RETURNING" in unique or "FORAGING" in unique):
            score = max(score, 0.5)
    return score


def colony_event_hints(
    *,
    schedule_phase: str,
    coherence: float,
    pressure: float,
    phase_deviation_score: float,
    observed_behavior: str,
    disturbance_active: bool,
) -> list[dict[str, str]]:
    """Lightweight event hints for TRER (type, message, severity)."""
    events: list[dict[str, str]] = []
    if schedule_phase == "roost_convergence" and observed_behavior in ("returning", "roosting"):
        events.append(
            {"type": "reorganization", "message": "Roost convergence underway", "severity": "low"}
        )
    if coherence >= 0.78:
        events.append(
            {"type": "reorganization", "message": "High colony coherence", "severity": "low"}
        )
    elif coherence < 0.38:
        events.append(
            {"type": "pressure_spike", "message": "Colony fragmented", "severity": "medium"}
        )
    if schedule_phase == "dispersal" and phase_deviation_score >= 0.55:
        events.append(
            {"type": "pressure_spike", "message": "Delayed dispersal", "severity": "medium"}
        )
    if pressure >= 0.58:
        events.append(
            {
                "type": "pressure_spike",
                "message": "Elevated roost pressure",
                "severity": "high" if pressure >= 0.72 else "medium",
            }
        )
    if disturbance_active:
        events.append(
            {"type": "rupture_warning", "message": "Disturbance response active", "severity": "medium"}
        )
    if schedule_phase == "settling" and phase_deviation_score < 0.35:
        events.append(
            {"type": "reorganization", "message": "Settling phase entered", "severity": "low"}
        )
    return events
