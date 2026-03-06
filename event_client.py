"""HTTP event client for metadata-only edge sensor events."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import time
from typing import Any

import requests


class EventClient:
    def __init__(
        self,
        endpoint: str,
        sensor_id: str,
        auth_cfg: dict[str, Any],
        retry_cfg: dict[str, Any],
        sequence_file: str,
        replay_log_path: str,
        dry_run: bool = False,
    ) -> None:
        self.endpoint = endpoint
        self.sensor_id = sensor_id
        self.auth_cfg = auth_cfg
        self.retry_cfg = retry_cfg
        self.dry_run = dry_run

        self.sequence_file = Path(sequence_file)
        self.replay_log_path = Path(replay_log_path)
        self.sequence_file.parent.mkdir(parents=True, exist_ok=True)
        self.replay_log_path.parent.mkdir(parents=True, exist_ok=True)

        self._last_sequence = self._load_sequence()

    def emit(self, event_type: str, confidence: float, payload: dict[str, Any]) -> dict[str, Any]:
        event = {
            "event_type": event_type,
            "sensor_id": self.sensor_id,
            "sequence": self._next_sequence(),
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "confidence": round(float(confidence), 3),
            "payload": payload,
        }

        if self.dry_run:
            print(f"[DRY_RUN] {json.dumps(event, separators=(',', ':'))}")
            self._append_replay(event, delivered=True, attempts=0, error=None)
            return event

        delivered, attempts, error = self._post_with_retry(event)
        self._append_replay(event, delivered=delivered, attempts=attempts, error=error)

        if delivered:
            print(
                f"[SEND_OK] sequence={event['sequence']} type={event_type} attempts={attempts}"
            )
        else:
            print(
                f"[SEND_FAIL] sequence={event['sequence']} type={event_type} "
                f"attempts={attempts} error={error}"
            )
        return event

    def _load_sequence(self) -> int:
        if not self.sequence_file.exists():
            return 0
        try:
            data = json.loads(self.sequence_file.read_text(encoding="utf-8"))
            return int(data.get("last_sequence", 0))
        except Exception:
            return 0

    def _next_sequence(self) -> int:
        self._last_sequence += 1
        payload = {"last_sequence": self._last_sequence}
        self.sequence_file.write_text(json.dumps(payload), encoding="utf-8")
        return self._last_sequence

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-Sensor-Id": self.sensor_id,
        }

        auth_type = str(self.auth_cfg.get("type", "bearer")).strip().lower()
        if auth_type == "bearer":
            token = self.auth_cfg.get("token", "")
            if token:
                headers["Authorization"] = f"Bearer {token}"
        elif auth_type == "shared_secret":
            secret = self.auth_cfg.get("secret", "")
            if secret:
                headers["X-Sensor-Secret"] = str(secret)
        return headers

    def _post_with_retry(self, event: dict[str, Any]) -> tuple[bool, int, str | None]:
        retries = int(self.retry_cfg.get("max_retries", 3))
        timeout_seconds = float(self.retry_cfg.get("request_timeout_seconds", 5.0))
        base_backoff = float(self.retry_cfg.get("backoff_initial_seconds", 0.5))
        max_backoff = float(self.retry_cfg.get("backoff_max_seconds", 5.0))

        attempts = 0
        error: str | None = None
        for attempt in range(1, retries + 2):
            attempts += 1
            try:
                response = requests.post(
                    self.endpoint,
                    headers=self._headers(),
                    json=event,
                    timeout=timeout_seconds,
                )
                if 200 <= response.status_code < 300:
                    return True, attempts, None
                error = f"http_{response.status_code}:{response.text[:200]}"
            except requests.RequestException as exc:
                error = str(exc)

            if attempt <= retries:
                sleep_seconds = min(max_backoff, base_backoff * (2 ** (attempt - 1)))
                time.sleep(sleep_seconds)

        return False, attempts, error

    def _append_replay(
        self,
        event: dict[str, Any],
        delivered: bool,
        attempts: int,
        error: str | None,
    ) -> None:
        record = {
            "event": event,
            "delivery": {
                "delivered": delivered,
                "attempts": attempts,
                "error": error,
                "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            },
        }
        with self.replay_log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")
