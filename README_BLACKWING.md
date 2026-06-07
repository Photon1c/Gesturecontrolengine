# Blackwing Pilot — Corvid Flight Lab

Camera-driven flock control using MediaPipe pose and a Three.js hangar viewer.
Internal code name: **Blackwing**. User-facing label: **Crow Hangar**.

## Design intent

This is a **body-to-bird flight controller**, not a static gesture classifier.

The core signal is **periodic vertical wrist velocity** (downstroke flapping), mapped to thrust and lift. Arm attitude provides bank, pitch, glide, and perch modes.

```
camera frame
  → MediaPipe pose
  → FlightController (wrist vy, wingspan, bank, pitch)
  → CrowPhysics
  → stable /api/state JSON
  → Three.js hangar + OpenCV HUD
```

## Run

**One-time hangar build** (Vite + React + Three.js ESM):

```bash
cd plugins/crow/hangar
npm install
npm run build
```

**Start the pilot:**

```bash
python sensor_engine.py --blackwing --blackwing-config blackwing_config.json
```

Open the hangar URL printed at startup (default `http://127.0.0.1:8765/`). The Python server auto-serves `plugins/crow/web/dist/` when built; otherwise it falls back to the legacy static hangar.

**Hangar dev mode** (hot reload, proxies `/api/state` to the pilot):

```bash
# terminal 1
python sensor_engine.py --blackwing

# terminal 2
cd plugins/crow/hangar && npm run dev
```

Then open `http://127.0.0.1:5173/`.

## Gesture mappings

| Movement | Crow behavior |
|----------|---------------|
| Both arms flapping rhythmically | Lift + thrust (`flap_power`) |
| Arms held wide | Glide |
| Left arm higher than right | Bank right |
| Right arm higher than left | Bank left |
| Hands forward (wrist z) | Dive / accelerate |
| Arms pulled in, low span | Perch / slow |

## Stable telemetry schema (`GET /api/state`)

`schema_version` is `1`. The HUD and hangar should treat these keys as stable:

```json
{
  "schema_version": 1,
  "meta": {
    "phase": "flying",
    "tracking": true,
    "hangar": "http://127.0.0.1:8765/",
    "mode": "flap",
    "hud": { "show_energy": true, "stall_warn_threshold": 0.55 }
  },
  "lead": {
    "position": [0, 12, -30],
    "velocity": [0, 1.2, -4.5],
    "rotation": [0.1, 0.4, -0.2]
  },
  "controls": {
    "flap_power": 0.72,
    "wingspan": 0.81,
    "bank": -0.23,
    "pitch": 0.18,
    "glide": true,
    "perch": false
  },
  "status": {
    "energy": 0.66,
    "lift": 0.74,
    "drag": 0.31,
    "stall_risk": 0.12
  },
  "flock": []
}
```

Colony extension (home base + agent states):

```json
{
  "colony": {
    "roost": {
      "name": "North Yard Roost",
      "location": [0, 8, 0],
      "population": 20,
      "territory_radius": 45
    },
    "greeting": "The roost recognizes Pilot.",
    "agents": [
      { "id": "lead", "state": "FOLLOWING_PLAYER", "energy": 0.78 }
    ]
  }
}
```

Optional extension (enable via `debug.enabled` in config — not shown on main HUD):

```json
{
  "debug": {
    "raw_left_wrist_y": 0.42,
    "raw_right_wrist_y": 0.39,
    "left_wrist_vy": -0.12,
    "right_wrist_vy": -0.15,
    "tracking_confidence": 0.91
  }
}
```

View debug in the hangar with `?debug=1` on the URL, or press `D` in the OpenCV window (console trace only).

### Field notes

- **`lead`** — piloted crow kinematics only (position, velocity, rotation).
- **`controls`** — normalized body inputs. `bank` is the smoothed left-minus-right wrist offset.
- **`status`** — derived flight feel metrics for HUD bars/warnings (all 0..1).
- **`meta`** — session/connection state. `phase` is `ready | searching | flying | glide | perch`.
- **`flock`** — renderer extension (wing phase, per-bird mode). Safe to ignore for a minimal HUD.
- **`debug`** — optional tracking diagnostics for tuning; diagnose physics vs camera issues.

## Config (`blackwing_config.json`)

| Section | Purpose |
|---------|---------|
| `telemetry` | Hangar host/port |
| `debug` | Optional wrist/velocity/confidence payload (`enabled: false` by default) |
| `controls` | Vy smoothing (attack/decay), glide hysteresis, flap response curve |
| `physics` | Lift/thrust bursts, glide cruise stability, energy floor, stall smoothing |
| `hud` | Visibility, stall warn/critical tiers, energy-low threshold |
| `roost` | Home base location, territory, population scores, pilot trust |

**Hangar map:** bottom-left, drag corner to resize, press **M** to hide/show. Shows roost, territory ring, heading arrow, and flight trail.

### Feel tuning knobs (current defaults)

| Goal | Knobs |
|------|-------|
| Rewarding flaps | `physics.flap_lift_burst`, `flap_thrust_burst`, `flap_reward_boost` |
| Stable glide | `physics.glide_cruise_speed`, `glide_speed_stability`, `glide_roll_damp` |
| Readable stall | `hud.stall_warn_threshold`, `stall_critical_threshold`, `physics.stall_smoothing` |
| Energy drain | `physics.energy_idle_drain`, `energy_power_floor`, `energy_flap_bonus` |
| Smooth wrists | `controls.vy_attack`, `vy_decay`, `flap_attack`, `flap_decay` |

Tune flight feel via config — not by reshaping the plugin layout.

## Plugin layout (intentionally flat)

```
plugins/crow/
  flight_controller.py
  physics.py
  pilot.py
  telemetry_server.py
  hangar/          # Vite + React source (npm run build → web/dist/)
  web/
    dist/          # built hangar (preferred at runtime)
```

No nested `core/` or `hud/` packages — the project has the right bones; iteration should focus on **flight feel** and **schema stability**.
