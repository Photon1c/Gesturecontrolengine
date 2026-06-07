# TRER Viz (`trerviz/`)

Lightweight real-time Python visualization layer that sits **on top of** the existing
Gesturecontrolengine / TRER plugin outputs. It does **not** modify the core sensor engine,
physics loop, or plugin internals.

## Architecture

```
Plugin state (HTTP / snapshot / demo)
        ↓
  state_client.py   — fetch + fallback chain
        ↓
   adapters.py     — plugin-specific → normalized VizPacket
        ↓
  dashboard.py      — Streamlit panels
        ↓
      app.py        — live dashboard
```

### Normalized packet (`schema.py`)

Each plugin is visualized through a common packet:

```json
{
  "schema_version": 1,
  "plugin": "crow",
  "timestamp": "2026-06-06T12:00:00+00:00",
  "phase": "surge",
  "metrics": {
    "pressure": 0.0,
    "criticality": 0.0,
    "velocity": 0.0,
    "acceleration": 0.0,
    "dissipation": 0.0,
    "rupture_risk": 0.0,
    "coherence": 0.0,
    "call_density": 0.0
  },
  "fields": [{ "id": "node", "x": 0, "y": 0, "pressure": 0.5, "radius": 2 }],
  "events": [{ "type": "pressure_spike", "message": "…", "severity": "medium" }]
}
```

Phases: `build_up | surge | peak | overextension | discharge | refractory | reorganization`

### Optional metrics (schema v1, backward-compatible)

| Metric | Range | Meaning |
|--------|-------|---------|
| `coherence` | 0.0–1.0 | Colony alignment: flock proximity, shared agent states, heading alignment, daily-cycle fit, calm flight. High = stable patrol or roost regroup; low = scattered/conflicting routines. |
| `call_density` | calls/min | Acoustic call rate readiness. Defaults to `0.0` until live audio or a snapshot provides it. |

Crow adapter reads `call_density` from (first match):

- `raw.audio.call_density`
- `raw.colony.call_density`
- `raw.colony.roost.call_density`

Infrastructure and market adapters leave both optional metrics at `0.0`.

## Data sources (priority)

1. **HTTP** — tiny-router style endpoints (Crow: `GET http://127.0.0.1:8765/api/state`)
2. **Snapshot files** — `trerviz/snapshots/{plugin}_latest.json`
3. **Demo mode** — synthetic packets when live data is unavailable

### gepa-viz / snapshot compatibility

Drop normalized or raw snapshots into `trerviz/snapshots/`:

| File | Purpose |
|------|---------|
| `crow_latest.json` | Raw Crow `/api/state` or normalized packet |
| `infrastructure_latest.json` | Infrastructure plugin export |
| `market_latest.json` | Market plugin export |
| `gepa/{plugin}_snapshot.json` | gepa-viz export path (optional) |

Set `"type": "snapshot"` in `trerviz/config.json` to skip HTTP.

## Run

```powershell
pip install -r requirements-trerviz.txt
```

**With live Crow data** (terminal 1):

```powershell
python sensor_engine.py --blackwing
```

**Visualizer** (terminal 2):

```powershell
streamlit run trerviz/app.py
```

Open the URL Streamlit prints (default `http://localhost:8501`).

## Configuration

Edit `trerviz/config.json`:

```json
{
  "sources": {
    "crow": { "type": "http", "url": "http://127.0.0.1:8765/api/state" },
    "infrastructure": { "type": "demo" },
    "market": { "type": "demo" }
  }
}
```

Source types: `http`, `snapshot`, `normalized`, `demo`

## Adding a new plugin (no core refactor)

1. Add a small adapter class in `adapters.py` mapping native state → `VizPacket`.
2. Register it in `ADAPTERS`.
3. Add a `sources.{plugin}` entry in `config.json`.
4. Optionally export snapshots to `trerviz/snapshots/{plugin}_latest.json`.

Prefer adapters over editing plugin code. Optional plugin-side export is only needed
if no HTTP or file snapshot exists yet.

## Dashboard panels

- Plugin selector (crow / infrastructure / market / all)
- Live metric cards (pressure, criticality, rupture risk, velocity, acceleration, dissipation)
- Time-series chart (pressure + criticality)
- 2D pressure field scatter
- Recent events panel
- Raw JSON packet viewer
