# Market attractor plugin

Models a symbol (default **SPY**) as an agent navigating **dynamic attractors** built from local stock and option CSV files. Emits TRER-compatible normalized packets for `trerviz`.

## Data paths

- Stock: `F:/inputs/stocks/{TICKER}.csv`
- Options: `F:/inputs/options/log/{ticker}/{mm_dd_yyyy}/{ticker}_quotedata.csv` (`skiprows=3`)

If options are missing, the model falls back to price-only attractors.

## CLI

```bash
python -m plugins.market.attractors --ticker SPY --emit-json
python -m plugins.market.attractors --ticker SPY --snapshot trerviz/snapshots/market_latest.json
```

## Blackwing modes

| Mode | Command | Behavior |
|------|---------|----------|
| **Static** (default) | `python sensor_engine.py --blackwing --static` | Fixed roost colony — existing crow simulation |
| **Dynamic** | `python sensor_engine.py --blackwing --dynamic` | Crow sim + market attractor sidecar; writes `trerviz/snapshots/market_latest.json` and adds `market` to `/api/state` |

Configure paths in `blackwing_config.json` → `market`.

## trerviz

Set `trerviz/config.json` → `sources.market.type` to `"snapshot"` to load `market_latest.json` without HTTP.
