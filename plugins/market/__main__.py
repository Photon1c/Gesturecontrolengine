"""CLI entry: python -m plugins.market.attractors --ticker SPY --emit-json"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .market_packet import build_and_write_snapshot, build_market_packet
from .attractors import compute_attractors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Market attractor model from local CSVs")
    parser.add_argument("--ticker", default="SPY", help="Ticker symbol")
    parser.add_argument("--stock-root", default="F:/inputs/stocks")
    parser.add_argument("--options-root", default="F:/inputs/options/log")
    parser.add_argument("--price-window", type=int, default=20)
    parser.add_argument("--emit-json", action="store_true", help="Print normalized packet JSON")
    parser.add_argument(
        "--snapshot",
        default="trerviz/snapshots/market_latest.json",
        help="Write latest packet for trerviz snapshot mode",
    )
    args = parser.parse_args(argv)

    cfg = {
        "ticker": args.ticker,
        "stock_root": args.stock_root,
        "options_root": args.options_root,
        "price_window": args.price_window,
        "snapshot_path": args.snapshot,
    }
    try:
        if args.emit_json and not args.snapshot:
            packet = build_market_packet(compute_attractors(cfg))
        else:
            packet = build_and_write_snapshot(cfg, snapshot_path=args.snapshot)
    except FileNotFoundError as exc:
        print(f"[market] {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[market] failed: {exc}", file=sys.stderr)
        return 1

    if args.emit_json:
        print(json.dumps(packet, indent=2))
    else:
        print(f"[market] wrote {Path(args.snapshot).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
