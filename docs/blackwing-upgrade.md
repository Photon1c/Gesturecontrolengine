We have TRER / Blackwing infrastructure with crow-style roost/attractor concepts. I want to add a market attractor modeling layer using my existing local stock and option CSV files.

Primary goal:
Model SPY as an agent navigating around one or more market attractors.

Core attractor metrics:
- attractor_strength
- attractor_velocity
- attractor_stability

Use existing CSV loaders if available, especially data_loader.py. Do not use yfinance or external APIs.

Known local data conventions:
- Stock CSV path:
  F:/inputs/stocks/{TICKER}.csv

- Stock columns usually include:
  Date, Close/Last, Volume, Open, High, Low

- Option chain CSV path:
  F:/inputs/options/log/{ticker_lower}/{mm_dd_yyyy}/{ticker_lower}_quotedata.csv

- Option chain CSVs often require skiprows=3

- Important option columns:
  Strike
  Expiration Date
  Calls: Bid, Ask, Volume, IV, Delta, Gamma, Open Interest
  Puts: Bid.1, Ask.1, Volume.1, IV.1, Delta.1, Gamma.1, Open Interest.1

Implementation constraint:
Do not refactor existing TRER or Blackwing code. Add a market plugin/module that can emit normalized packets compatible with trerviz.

Suggested files:
plugins/market/
  __init__.py
  attractors.py
  market_packet.py
  README.md

or fit the repo’s existing plugin layout if it already has a market plugin.

Model concept:
The market “roost” is not fixed. It is a dynamic attractor.

For SPY:
- price = lead agent position
- attractor position = equilibrium basin / gamma basin / liquidity center
- distance_from_attractor = price - attractor_position
- attractor_strength = how strongly current options/volume structure pulls price toward the attractor
- attractor_velocity = how fast the attractor itself is moving across recent sessions
- attractor_stability = how persistent and well-supported the attractor is

Attractor candidates:
1. Price-based attractor:
   rolling VWAP or rolling mean of Close/Last

2. Options-based attractor:
   strike with highest combined open interest and/or gamma concentration near spot

3. Hybrid attractor:
   weighted blend of price-based and options-based attractors

Suggested formulas, keep simple:

price_attractor:
- rolling_mean_close over configurable window, default 20 sessions
- optional rolling VWAP if Volume is available

options_attractor:
- compute per-strike concentration:
  call_oi + put_oi + abs(call_gamma * call_oi) + abs(put_gamma * put_oi)
- choose strike with highest concentration near spot
- optionally restrict to strikes within ±8% of spot

hybrid_attractor:
- blend price_attractor and options_attractor
- if options data is missing, fall back to price_attractor

attractor_strength:
- normalized concentration of top attractor strike versus total nearby concentration
- blend with inverse distance from current price
- range 0.0–1.0

attractor_velocity:
- change in attractor_position over last N sessions or available snapshots
- if only one option chain snapshot exists, use rolling price attractor velocity
- return signed value

attractor_stability:
- high when attractor position persists near same level over recent sessions
- high when top concentration clearly dominates neighboring strikes
- low when attractor jumps around or concentration is diffuse
- range 0.0–1.0

Normalized packet output:
Emit a TRER-compatible packet like:

{
  "schema_version": 1,
  "plugin": "market",
  "symbol": "SPY",
  "timestamp": "...",
  "phase": "patrol | compression | convergence | rupture | reorganization",
  "metrics": {
    "pressure": 0.0,
    "coherence": 0.0,
    "criticality": 0.0,
    "dissipation": 0.0,
    "velocity": 0.0,
    "acceleration": 0.0,
    "rupture_risk": 0.0,
    "attractor_strength": 0.0,
    "attractor_velocity": 0.0,
    "attractor_stability": 0.0
  },
  "attractors": [
    {
      "id": "spy_hybrid_attractor",
      "type": "dynamic",
      "position": 0.0,
      "strength": 0.0,
      "velocity": 0.0,
      "stability": 0.0,
      "source": "hybrid"
    }
  ],
  "fields": [
    {
      "id": "SPY",
      "x": 0.0,
      "y": 0.0,
      "pressure": 0.0,
      "radius": 1.0,
      "label": "SPY"
    },
    {
      "id": "spy_hybrid_attractor",
      "x": 0.0,
      "y": 0.0,
      "pressure": 0.0,
      "radius": 1.0,
      "label": "Hybrid attractor"
    }
  ],
  "events": [],
  "raw": {}
}

Market pressure:
- increases as price moves far from attractor
- increases when volatility expands
- increases when attractor stability drops
- increases when options attractor is strong but price is escaping it

Market coherence:
- high when price, rolling trend, and options attractor agree
- low when price diverges strongly from options attractor or attractor jumps

Criticality:
- high when pressure is high, stability is low, and velocity/acceleration are high
- especially high when price crosses away from a strong attractor

Dissipation:
- high when price returns toward attractor
- high when volatility compresses
- high when attractor is stable and pressure is falling

Phase mapping:
- patrol: price moving near attractor with low pressure
- compression: strong attractor + narrowing range
- convergence: price moving toward attractor
- rupture: price escaping strong attractor with rising velocity
- reorganization: old attractor weakened and new attractor forming

CLI:
Add a small CLI runner if appropriate:

python -m plugins.market.attractors --ticker SPY --emit-json

Optional:
Write latest packet to:
trerviz/snapshots/market_latest.json

So trerviz can load it without needing an HTTP server.

Success criteria:
- Running the module against local SPY stock/options CSVs produces one normalized market packet.
- If options data is missing, the script still works with stock data only.
- No external data downloads.
- No major dependency additions beyond pandas/numpy.
- Simple formulas are documented in comments.

## Blackwing CLI modes

| Flag | Behavior |
|------|----------|
| `--static` (default) | Fixed roost crow colony — existing Blackwing simulation |
| `--dynamic` | Adds market attractor sidecar from local CSVs; writes `trerviz/snapshots/market_latest.json` and exposes `market` on `/api/state` |

```powershell
python sensor_engine.py --blackwing --static --blackwing-config blackwing_config.json
python sensor_engine.py --blackwing --dynamic --blackwing-config blackwing_config.json
python -m plugins.market.attractors --ticker SPY --emit-json
```