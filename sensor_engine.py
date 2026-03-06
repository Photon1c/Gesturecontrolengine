"""Windows-first MediaPipe edge sensor engine.

Captures frames locally, performs local detection, and emits metadata-only events.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import time
from typing import Any

from event_client import EventClient
from gesture_detector import GestureDetector
from presence_detector import PresenceDetector


class ArmStateMachine:
    """Local mirror of execute-arming flow for operator observability.

    This machine does not execute actions; it only tracks state and logs transitions.
    """

    def __init__(self, confirm_window_seconds: float) -> None:
        self.state = "IDLE"
        self.confirm_window_seconds = confirm_window_seconds
        self.armed_since: float | None = None

    def on_gesture(self, gesture: str, ts: float) -> str | None:
        if self.state == "IDLE":
            if gesture == "arm_execute":
                self.state = "ARMED"
                self.armed_since = ts
                return "IDLE -> ARMED"
            if gesture == "pause":
                self.state = "PAUSED"
                return "IDLE -> PAUSED"
            return None

        if self.state == "ARMED":
            if gesture == "confirm_execute":
                if self.armed_since is not None and (ts - self.armed_since) <= self.confirm_window_seconds:
                    self.state = "IDLE"
                    self.armed_since = None
                    return "ARMED -> TRIGGER_ELIGIBLE -> IDLE"
                self.state = "IDLE"
                self.armed_since = None
                return "ARMED -> IDLE (confirm timeout)"
            if gesture == "cancel":
                self.state = "IDLE"
                self.armed_since = None
                return "ARMED -> IDLE (cancel)"
            if gesture == "pause":
                self.state = "PAUSED"
                return "ARMED -> PAUSED"
            return None

        if self.state == "PAUSED":
            if gesture in {"cancel", "pause"}:
                self.state = "IDLE"
                self.armed_since = None
                return "PAUSED -> IDLE"
            if gesture == "arm_execute":
                self.state = "ARMED"
                self.armed_since = ts
                return "PAUSED -> ARMED"
            return None

        return None

    def on_tick(self, ts: float) -> str | None:
        if self.state == "ARMED" and self.armed_since is not None:
            if (ts - self.armed_since) > self.confirm_window_seconds:
                self.state = "IDLE"
                self.armed_since = None
                return "ARMED -> IDLE (window timeout)"
        return None


def load_config(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def build_client(config: dict[str, Any], dry_run: bool) -> EventClient:
    transport = config["transport"]
    return EventClient(
        endpoint=transport["endpoint"],
        sensor_id=config["sensor"]["sensor_id"],
        auth_cfg=transport.get("auth", {}),
        retry_cfg=transport.get("retry", {}),
        sequence_file=config["logging"]["sequence_file"],
        replay_log_path=config["logging"]["replay_jsonl"],
        dry_run=dry_run,
    )


def run_test_events(client: EventClient, cycles: int, sleep_seconds: float) -> None:
    script = [
        ("presence.state_changed", 0.95, {"state": "at_terminal"}),
        ("gesture.detected", 0.9, {"gesture": "arm_execute"}),
        ("gesture.detected", 0.92, {"gesture": "confirm_execute"}),
        ("sensor.heartbeat", 1.0, {"status": "ok"}),
    ]
    print(f"[TEST_MODE] emitting scripted events cycles={cycles}")
    for _ in range(cycles):
        for event_type, confidence, payload in script:
            client.emit(event_type, confidence, payload)
            time.sleep(sleep_seconds)


def run_camera_loop(config: dict[str, Any], client: EventClient, debug_overlay: bool) -> None:
    try:
        import cv2  # type: ignore
        import mediapipe as mp  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependencies for camera mode. Install requirements first."
        ) from exc

    sensor_cfg = config["sensor"]
    presence = PresenceDetector(config.get("presence", {}))
    gesture = GestureDetector(config.get("gesture", {}))
    machine = ArmStateMachine(
        confirm_window_seconds=float(config.get("policy", {}).get("confirm_window_seconds", 8.0))
    )

    camera_index = int(sensor_cfg.get("camera_index", 0))
    if platform.system().lower().startswith("win"):
        cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
    else:
        cap = cv2.VideoCapture(camera_index)

    if not cap.isOpened():
        raise RuntimeError(f"Could not open camera index {camera_index}")

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(sensor_cfg.get("frame_width", 640)))
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(sensor_cfg.get("frame_height", 480)))

    mp_pose = mp.solutions.pose
    mp_hands = mp.solutions.hands
    mp_drawing = mp.solutions.drawing_utils

    last_heartbeat = 0.0
    heartbeat_seconds = float(config["sensor"].get("heartbeat_seconds", 15))
    mirror = bool(sensor_cfg.get("mirror_preview", True))
    draw_landmarks = bool(config.get("debug", {}).get("draw_landmarks", False))

    with mp_pose.Pose(
        min_detection_confidence=float(sensor_cfg.get("pose_detection_confidence", 0.5)),
        min_tracking_confidence=float(sensor_cfg.get("pose_tracking_confidence", 0.5)),
    ) as pose_model, mp_hands.Hands(
        max_num_hands=int(sensor_cfg.get("max_num_hands", 2)),
        min_detection_confidence=float(sensor_cfg.get("hand_detection_confidence", 0.5)),
        min_tracking_confidence=float(sensor_cfg.get("hand_tracking_confidence", 0.5)),
    ) as hands_model:
        print("[INFO] camera loop started (press q in debug window to quit)")
        while True:
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.05)
                continue

            if mirror:
                frame = cv2.flip(frame, 1)

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            rgb.flags.writeable = False
            pose_result = pose_model.process(rgb)
            hands_result = hands_model.process(rgb)
            rgb.flags.writeable = True

            now = time.time()
            transition = presence.update(now, pose_result, hands_result)
            if transition is not None:
                print(f"[PRESENCE] state={transition.state} confidence={transition.confidence}")
                client.emit(
                    "presence.state_changed",
                    transition.confidence,
                    {"state": transition.state},
                )

            gestures = gesture.update(now, pose_result, hands_result)
            for item in gestures:
                print(f"[GESTURE] gesture={item.gesture} confidence={item.confidence}")
                client.emit(
                    "gesture.detected",
                    item.confidence,
                    {"gesture": item.gesture},
                )
                sm_transition = machine.on_gesture(item.gesture, now)
                if sm_transition:
                    print(f"[STATE_MACHINE] {sm_transition}")

            timeout_transition = machine.on_tick(now)
            if timeout_transition:
                print(f"[STATE_MACHINE] {timeout_transition}")

            if (now - last_heartbeat) >= heartbeat_seconds:
                client.emit(
                    "sensor.heartbeat",
                    1.0,
                    {
                        "status": "ok",
                        "presence_state": presence.state,
                        "arm_state": machine.state,
                        "raw_video_exported": False,
                    },
                )
                last_heartbeat = now

            if debug_overlay:
                text_lines = [
                    f"presence: {presence.state}",
                    f"arm_state: {machine.state}",
                    f"sensor: {sensor_cfg.get('sensor_id')}",
                    "No raw frames leave this machine",
                ]
                y = 24
                for line in text_lines:
                    cv2.putText(
                        frame,
                        line,
                        (12, y),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.6,
                        (80, 255, 80),
                        2,
                        cv2.LINE_AA,
                    )
                    y += 24

                if draw_landmarks:
                    if pose_result.pose_landmarks:
                        mp_drawing.draw_landmarks(
                            frame,
                            pose_result.pose_landmarks,
                            mp_pose.POSE_CONNECTIONS,
                        )
                    if hands_result.multi_hand_landmarks:
                        for hand in hands_result.multi_hand_landmarks:
                            mp_drawing.draw_landmarks(
                                frame,
                                hand,
                                mp_hands.HAND_CONNECTIONS,
                            )

                cv2.imshow("MediaPipe Edge Sensor", frame)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break

    cap.release()
    if debug_overlay:
        cv2.destroyAllWindows()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MediaPipe gesture + presence edge sensor")
    parser.add_argument("--config", default="config.json", help="Path to config JSON")
    parser.add_argument("--dry-run", action="store_true", help="Print events but do not send")
    parser.add_argument(
        "--debug-overlay",
        action="store_true",
        help="Show local OpenCV overlay window (local only)",
    )
    parser.add_argument(
        "--test-events",
        action="store_true",
        help="Emit synthetic events without camera (for VPS ingestion testing)",
    )
    parser.add_argument(
        "--test-cycles",
        type=int,
        default=1,
        help="How many scripted test cycles to emit in --test-events mode",
    )
    parser.add_argument(
        "--test-sleep-seconds",
        type=float,
        default=1.0,
        help="Delay between synthetic test events",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg_path = Path(args.config)
    config = load_config(str(cfg_path))
    client = build_client(config, dry_run=args.dry_run)

    if args.test_events:
        run_test_events(client, cycles=args.test_cycles, sleep_seconds=args.test_sleep_seconds)
        return

    run_camera_loop(config, client, debug_overlay=args.debug_overlay)


if __name__ == "__main__":
    main()
