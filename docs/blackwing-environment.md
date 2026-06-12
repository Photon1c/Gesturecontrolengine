# Blackwing Environmental Interaction Layer

Field-driven upgrade: resource nodes, threat detection, sentinel alert propagation, and coordinated flock escape.

Inspired by real observations (garden foraging ~12–3 PM, scout alert, coordinated scatter when a human appears).

## Architecture

Same multi-attractor model as the market plugin:

| Node | Type | Role |
|------|------|------|
| North Yard Roost | `fixed` | Home tree (existing roost) |
| Garden | `resource` (food) | Open foraging attractor during patrol |
| Garden fountain | `resource` (water) | Capacity 1, **turn-taking**; lowers pressure, raises convergence |
| Human presence | `threat` | Activated when pose tracking appears in **auto mode** |

## Behavior flow

```
Patrol/forage → fly to garden / fountain resource nodes
Fountain: one DRINKING, others WAITING in ring queue (turn-taking)
Human enters frame (pose tracking) in auto mode
  → scout/sentinel within alert_radius detects threat
  → ~120ms reaction → alert propagates (~280ms)
  → flock state: ALARMED → ESCAPE
  → coordinated scatter away from threat node
  → colony events: resource_visit_interrupted, colony_alert, stress_evacuation
```

## Config (`blackwing_config.json` → `environment`)

```json
"environment": {
  "enabled": true,
  "nodes": [ ... ],
  "sentinel": {
    "alert_radius": 15.0,
    "reaction_time_ms": 120,
    "propagation_ms": 280
  },
  "escape": {
    "duration_seconds": 8.0,
    "flee_distance": 28.0,
    "scatter_spread": 4.5
  }
}
```

Set `"enabled": false` to restore pre-environment behavior.

## Telemetry (`/api/state`)

Backward compatible additions under `colony`:

- `colony.environment` — nodes, threat/escape flags, events
- `colony.information_velocity` — reflexivity proxy (alert propagation speed)
- `colony.events` — includes `resource_visit_interrupted`, `colony_alert`, `stress_evacuation`, `water_convergence`, `turn_taking`
- `colony.environment.resource_sites[]` — occupant + wait queue per capacity-limited node
- Agent states **`ESCAPE`**, **`DRINKING`**, **`WAITING`** alongside **`ALARMED`**

## How to test

1. Start Blackwing in **auto mode** (default):  
   `python sensor_engine.py --blackwing --static`
2. Wait for **patrol/foraging** phase (or set `simulation.start_hour` to `13`).
3. Crows should transit toward the **garden** node.
4. Step into camera frame — pose tracking activates the **human_leslie** threat.
5. Watch flock scatter; check `/api/state` for `escape_active: true` and colony events.

Press **A** for manual control — threat/escape logic is suppressed while you fly manually.

## Files

| File | Purpose |
|------|---------|
| `plugins/crow/environment.py` | Nodes, sentinel alerts, escape targets |
| `plugins/crow/roost.py` | Colony integration, routine targets, telemetry |
| `plugins/crow/auto_pilot.py` | Escape flight (high flap, flee heading) |
| `plugins/crow/physics.py` | Escape guidance + follower scatter |
| `plugins/crow/colony_cycle.py` | ESCAPE-aware observed behavior |

## Future (not in this pass)

- Audio call propagation
- Per-role `reaction_time_ms` overrides
- Map editor / multiple resource nodes with priority

## Water fountain (`garden_fountain`)

Capacity-limited **water** resource with **turn-taking**:

```json
{
  "id": "garden_fountain",
  "type": "resource",
  "resource_type": "water",
  "attractor_strength": 0.85,
  "capacity": 1,
  "recharge_rate": 1.0,
  "social_rule": "turn_taking",
  "pressure_effect": -0.15,
  "coherence_effect": 0.10
}
```

- One crow **DRINKING** at the fountain; others **WAITING** in a ring queue
- Active drinking lowers colony **pressure**, raises local **coherence**
- Events: `water_convergence`, `turn_taking`
- Telemetry: `colony.environment.resource_sites[]` with occupant + queue

During foraging/patrol, crows prefer the fountain when strength + visitor density is high.

**Market mapping:** fountain = liquidity pool; turn queue = order flow; sentinel alert = volatility shock; garden = temporary trading venue; roost = home equilibrium.
