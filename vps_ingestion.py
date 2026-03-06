"""VPS-side ingestion handler for Conferenceroom sensor events.

This service accepts metadata-only events from edge sensors, applies policy,
and only allows trigger eligibility for the `zeroclaw_smoke` workflow.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
import threading
import time
from typing import Any

from flask import Flask, jsonify, request
import requests


ALLOWED_EVENT_TYPES = {
    "presence.state_changed",
    "gesture.detected",
    "sensor.heartbeat",
}
ALLOWED_PRESENCE_STATES = {"at_terminal", "away", "resting", "asleep", "unknown"}
ALLOWED_GESTURES = {"arm_execute", "confirm_execute", "pause", "cancel"}
FORBIDDEN_PAYLOAD_KEYS = {"frame", "image", "video", "raw_frame", "raw_image", "raw_video"}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_config(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


@dataclass
class SensorRuntimeState:
    last_sequence: int = 0
    latest_presence_state: str = "unknown"
    arm_state: str = "IDLE"
    armed_since_epoch: float | None = None
    last_event_ts: str | None = None
    last_reason: str | None = None


class WorkflowTriggerClient:
    """Outbound workflow trigger client for policy-approved events only."""

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg

    def trigger_zeroclaw_smoke(
        self, sensor_id: str, source_event: dict[str, Any]
    ) -> tuple[bool, str]:
        target = str(self.cfg.get("allowed_workflow", "zeroclaw_smoke"))
        if target != "zeroclaw_smoke":
            return False, "workflow_not_allowed_by_config"

        mode = str(self.cfg.get("trigger_mode", "log_only")).strip().lower()
        if mode == "log_only":
            print(
                f"[TRIGGER_SIM] workflow=zeroclaw_smoke sensor_id={sensor_id} "
                f"sequence={source_event.get('sequence')}"
            )
            return True, "trigger_simulated"

        if mode != "http_post":
            return False, f"unsupported_trigger_mode:{mode}"

        endpoint = str(self.cfg.get("http_endpoint", "")).strip()
        if not endpoint:
            return False, "missing_trigger_http_endpoint"

        body = {
            "workflow": "zeroclaw_smoke",
            "sensor_id": sensor_id,
            "source_event_type": source_event.get("event_type"),
            "source_sequence": source_event.get("sequence"),
            "source_ts": source_event.get("ts"),
            "received_at": now_iso(),
        }

        headers = {"Content-Type": "application/json"}
        token = str(self.cfg.get("http_bearer_token", "")).strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        timeout = float(self.cfg.get("http_timeout_seconds", 5.0))
        try:
            response = requests.post(endpoint, json=body, headers=headers, timeout=timeout)
            if 200 <= response.status_code < 300:
                return True, "trigger_http_ok"
            return False, f"trigger_http_error:{response.status_code}"
        except requests.RequestException as exc:
            return False, f"trigger_http_exception:{exc}"


class IngestionEngine:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        self._lock = threading.Lock()
        self._states: dict[str, SensorRuntimeState] = {}
        self._workflow = WorkflowTriggerClient(cfg.get("workflow", {}))

        log_path = Path(cfg.get("logging", {}).get("decision_jsonl", "./logs/vps_ingestion.jsonl"))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._decision_log_path = log_path

        self._confirm_window = float(cfg.get("policy", {}).get("confirm_window_seconds", 8.0))

    def auth_ok(self, headers: Any) -> bool:
        auth_cfg = self.cfg.get("auth", {})
        auth_type = str(auth_cfg.get("type", "bearer")).strip().lower()
        if auth_type == "none":
            return True

        if auth_type == "bearer":
            expected = str(auth_cfg.get("token", ""))
            provided = str(headers.get("Authorization", ""))
            return bool(expected) and provided == f"Bearer {expected}"

        if auth_type == "shared_secret":
            expected = str(auth_cfg.get("secret", ""))
            provided = str(headers.get("X-Sensor-Secret", ""))
            return bool(expected) and provided == expected

        return False

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {sensor_id: asdict(state) for sensor_id, state in self._states.items()}

    def process_event(self, event: dict[str, Any]) -> tuple[dict[str, Any], int]:
        validation_error = self._validate_event(event)
        if validation_error:
            decision = self._decision(event, accepted=False, reason=validation_error)
            self._log_decision(decision, sensor_state=None)
            return decision, 400

        sensor_id = str(event["sensor_id"])
        sequence = int(event["sequence"])
        event_type = str(event["event_type"])
        payload = event["payload"]

        with self._lock:
            state = self._states.setdefault(sensor_id, SensorRuntimeState())
            self._expire_arm_if_needed(state)

            if sequence <= state.last_sequence:
                decision = self._decision(
                    event,
                    accepted=False,
                    reason="duplicate_or_out_of_order_sequence",
                    sensor_state=state,
                )
                self._log_decision(decision, sensor_state=asdict(state))
                return decision, 409

            # Reserve this sequence so replay does not pass even if policy rejects.
            state.last_sequence = sequence
            state.last_event_ts = str(event.get("ts", ""))

            if event_type == "presence.state_changed":
                next_state = str(payload.get("state", ""))
                if next_state not in ALLOWED_PRESENCE_STATES:
                    decision = self._decision(
                        event,
                        accepted=False,
                        reason="invalid_presence_state",
                        sensor_state=state,
                    )
                    state.last_reason = decision["reason"]
                    self._log_decision(decision, sensor_state=asdict(state))
                    return decision, 400

                changed = next_state != state.latest_presence_state
                state.latest_presence_state = next_state
                reason = "presence_state_updated" if changed else "presence_state_unchanged"
                decision = self._decision(event, accepted=True, reason=reason, sensor_state=state)
                state.last_reason = decision["reason"]
                self._log_decision(decision, sensor_state=asdict(state))
                return decision, 200

            if event_type == "sensor.heartbeat":
                decision = self._decision(
                    event,
                    accepted=True,
                    reason="heartbeat_ok",
                    sensor_state=state,
                )
                state.last_reason = decision["reason"]
                self._log_decision(decision, sensor_state=asdict(state))
                return decision, 200

            if event_type == "gesture.detected":
                gesture = str(payload.get("gesture", ""))
                if gesture not in ALLOWED_GESTURES:
                    decision = self._decision(
                        event,
                        accepted=False,
                        reason="invalid_gesture",
                        sensor_state=state,
                    )
                    state.last_reason = decision["reason"]
                    self._log_decision(decision, sensor_state=asdict(state))
                    return decision, 400

                decision, status = self._apply_gesture_policy(event, state, gesture)
                state.last_reason = decision["reason"]
                self._log_decision(decision, sensor_state=asdict(state))
                return decision, status

            decision = self._decision(
                event,
                accepted=False,
                reason="unsupported_event_type",
                sensor_state=state,
            )
            state.last_reason = decision["reason"]
            self._log_decision(decision, sensor_state=asdict(state))
            return decision, 400

    def _validate_event(self, event: dict[str, Any]) -> str | None:
        required = {"event_type", "sensor_id", "sequence", "ts", "confidence", "payload"}
        if not isinstance(event, dict):
            return "invalid_json_object"
        missing = sorted(required - set(event.keys()))
        if missing:
            return f"missing_fields:{','.join(missing)}"

        if str(event.get("event_type", "")) not in ALLOWED_EVENT_TYPES:
            return "invalid_event_type"
        if not str(event.get("sensor_id", "")).strip():
            return "invalid_sensor_id"
        try:
            sequence = int(event.get("sequence"))
            if sequence < 1:
                return "invalid_sequence"
        except Exception:
            return "invalid_sequence"

        confidence_raw = event.get("confidence")
        try:
            confidence = float(confidence_raw)
            if confidence < 0.0 or confidence > 1.0:
                return "invalid_confidence"
        except Exception:
            return "invalid_confidence"

        payload = event.get("payload")
        if not isinstance(payload, dict):
            return "invalid_payload"
        lower_keys = {str(k).strip().lower() for k in payload.keys()}
        if lower_keys & FORBIDDEN_PAYLOAD_KEYS:
            return "raw_media_not_allowed"
        return None

    def _expire_arm_if_needed(self, state: SensorRuntimeState) -> None:
        if state.arm_state != "ARMED" or state.armed_since_epoch is None:
            return
        if (time.time() - state.armed_since_epoch) > self._confirm_window:
            state.arm_state = "IDLE"
            state.armed_since_epoch = None
            state.last_reason = "arm_window_expired"

    def _apply_gesture_policy(
        self, event: dict[str, Any], state: SensorRuntimeState, gesture: str
    ) -> tuple[dict[str, Any], int]:
        sensor_id = str(event["sensor_id"])
        now_epoch = time.time()

        if gesture == "arm_execute":
            state.arm_state = "ARMED"
            state.armed_since_epoch = now_epoch
            return (
                self._decision(event, accepted=True, reason="armed", sensor_state=state),
                200,
            )

        if gesture == "cancel":
            state.arm_state = "IDLE"
            state.armed_since_epoch = None
            return (
                self._decision(event, accepted=True, reason="cancelled_to_idle", sensor_state=state),
                200,
            )

        if gesture == "pause":
            if state.arm_state == "PAUSED":
                return (
                    self._decision(
                        event, accepted=True, reason="already_paused", sensor_state=state
                    ),
                    200,
                )
            state.arm_state = "PAUSED"
            state.armed_since_epoch = None
            return (
                self._decision(event, accepted=True, reason="paused", sensor_state=state),
                200,
            )

        # confirm_execute
        if state.arm_state != "ARMED" or state.armed_since_epoch is None:
            return (
                self._decision(
                    event,
                    accepted=False,
                    reason="confirm_rejected_not_armed",
                    sensor_state=state,
                ),
                403,
            )

        if (now_epoch - state.armed_since_epoch) > self._confirm_window:
            state.arm_state = "IDLE"
            state.armed_since_epoch = None
            return (
                self._decision(
                    event,
                    accepted=False,
                    reason="confirm_rejected_window_expired",
                    sensor_state=state,
                ),
                403,
            )

        if state.latest_presence_state != "at_terminal":
            state.arm_state = "IDLE"
            state.armed_since_epoch = None
            return (
                self._decision(
                    event,
                    accepted=False,
                    reason=f"confirm_rejected_presence_{state.latest_presence_state}",
                    sensor_state=state,
                ),
                403,
            )

        triggered, trigger_reason = self._workflow.trigger_zeroclaw_smoke(sensor_id, event)
        state.arm_state = "IDLE"
        state.armed_since_epoch = None
        if not triggered:
            return (
                self._decision(
                    event,
                    accepted=False,
                    reason=f"workflow_trigger_failed:{trigger_reason}",
                    sensor_state=state,
                    triggered=False,
                ),
                502,
            )

        return (
            self._decision(
                event,
                accepted=True,
                reason=f"workflow_triggered:{trigger_reason}",
                sensor_state=state,
                triggered=True,
                workflow="zeroclaw_smoke",
            ),
            200,
        )

    def _decision(
        self,
        event: dict[str, Any],
        accepted: bool,
        reason: str,
        sensor_state: SensorRuntimeState | None = None,
        triggered: bool = False,
        workflow: str | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "accepted": accepted,
            "reason": reason,
            "triggered": triggered,
            "workflow": workflow,
            "event_type": event.get("event_type"),
            "sensor_id": event.get("sensor_id"),
            "sequence": event.get("sequence"),
            "received_at": now_iso(),
        }
        if sensor_state is not None:
            result["sensor_state"] = {
                "latest_presence_state": sensor_state.latest_presence_state,
                "arm_state": sensor_state.arm_state,
                "last_sequence": sensor_state.last_sequence,
            }
        return result

    def _log_decision(self, decision: dict[str, Any], sensor_state: dict[str, Any] | None) -> None:
        record = {
            "decision": decision,
            "sensor_state_snapshot": sensor_state,
            "logged_at": now_iso(),
        }
        with self._decision_log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")


def create_app(config_path: str) -> Flask:
    cfg = load_config(config_path)
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = int(cfg.get("server", {}).get("max_request_bytes", 32768))
    engine = IngestionEngine(cfg)

    @app.get("/healthz")
    def healthz() -> Any:
        return jsonify(
            {
                "status": "ok",
                "service": "conferenceroom-sensor-ingestion",
                "ts": now_iso(),
            }
        )

    @app.get("/conferenceroom/sensors/state")
    def sensor_state() -> Any:
        # Optional read endpoint for observability.
        return jsonify({"sensors": engine.snapshot(), "ts": now_iso()})

    @app.post("/conferenceroom/sensors/event")
    def ingest_event() -> Any:
        if not engine.auth_ok(request.headers):
            return (
                jsonify(
                    {
                        "accepted": False,
                        "triggered": False,
                        "reason": "unauthorized",
                        "received_at": now_iso(),
                    }
                ),
                401,
            )

        event = request.get_json(silent=True)
        if not isinstance(event, dict):
            return (
                jsonify(
                    {
                        "accepted": False,
                        "triggered": False,
                        "reason": "invalid_json",
                        "received_at": now_iso(),
                    }
                ),
                400,
            )

        decision, status = engine.process_event(event)
        return jsonify(decision), status

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Conferenceroom VPS sensor ingestion handler (MVP policy gate)"
    )
    parser.add_argument("--config", default="vps_config.json", help="Path to VPS config JSON")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    app = create_app(args.config)
    host = str(cfg.get("server", {}).get("host", "0.0.0.0"))
    port = int(cfg.get("server", {}).get("port", 8080))
    debug = bool(cfg.get("server", {}).get("debug", False))
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    main()
