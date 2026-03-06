"""Lightweight monitor for VPS ingestion decision logs."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Any


def parse_iso(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize VPS ingestion decisions from JSONL")
    parser.add_argument(
        "--log",
        default="./logs/vps_ingestion_decisions.jsonl",
        help="Path to JSONL decision log",
    )
    parser.add_argument(
        "--minutes",
        type=int,
        default=30,
        help="Only include records within this many minutes (0 means all)",
    )
    parser.add_argument(
        "--tail",
        type=int,
        default=10000,
        help="Max records to process from end of file (best-effort)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    path = Path(args.log)
    if not path.exists():
        print(f"[MONITOR] log not found: {path}")
        return

    cutoff: datetime | None = None
    if args.minutes > 0:
        cutoff = datetime.now().astimezone() - timedelta(minutes=args.minutes)

    accepted = 0
    rejected = 0
    triggered = 0
    reason_counts: Counter[str] = Counter()
    sensor_counts: Counter[str] = Counter()
    total = 0

    with path.open("r", encoding="utf-8") as fh:
        lines = fh.readlines()
    if args.tail > 0:
        lines = lines[-args.tail :]

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj: dict[str, Any] = json.loads(line)
        except Exception:
            continue

        decision = obj.get("decision")
        if not isinstance(decision, dict):
            continue

        received_at = parse_iso(str(decision.get("received_at", "")))
        if cutoff is not None and received_at is not None and received_at < cutoff:
            continue

        total += 1
        is_accepted = bool(decision.get("accepted", False))
        if is_accepted:
            accepted += 1
        else:
            rejected += 1

        if bool(decision.get("triggered", False)):
            triggered += 1

        reason = str(decision.get("reason", "unknown"))
        sensor_id = str(decision.get("sensor_id", "unknown"))
        reason_counts[reason] += 1
        sensor_counts[sensor_id] += 1

    print(f"[MONITOR] records={total} accepted={accepted} rejected={rejected} triggered={triggered}")
    if total == 0:
        return

    print("[MONITOR] top rejection/decision reasons:")
    for reason, count in reason_counts.most_common(10):
        print(f"  - {reason}: {count}")

    print("[MONITOR] per-sensor event counts:")
    for sensor, count in sensor_counts.most_common(10):
        print(f"  - {sensor}: {count}")


if __name__ == "__main__":
    main()
