"""Windows-first MediaPipe edge sensor engine.

Captures frames locally, performs local detection, and emits metadata-only events.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import sys
import time
from typing import Any

# Before MediaPipe (or TensorFlow) loads — classic mp.solutions pulls TF lazily.
os.environ.setdefault("TF_ENABLE_ONEDNN_OPTS", "0")

import numpy as np

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


def save_config(path: str, config: dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2)
        fh.write("\n")


def configure_opencv_io_logging(*, quiet: bool) -> None:
    """Reduce MSMF/DSHOW/orbbec spam when probing non-existent camera indices (Windows)."""
    import cv2  # type: ignore

    try:
        logging = cv2.utils.logging
        level = logging.LOG_LEVEL_SILENT if quiet else logging.LOG_LEVEL_WARNING
        logging.setLogLevel(level)
    except Exception:
        try:
            cv2.setLogLevel(0 if quiet else 3)
        except Exception:
            pass


def open_video_capture(camera_index: int) -> Any:
    """Open a camera; on Windows try DirectShow first, then fall back to default backend."""
    import cv2  # type: ignore

    if platform.system().lower().startswith("win"):
        cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
        if cap.isOpened():
            return cap
        cap.release()
    return cv2.VideoCapture(camera_index)


def probe_camera_readable(camera_index: int) -> bool:
    """Return True if this index yields a frame.

    On Windows, DirectShow can block a long time on empty indices; probe MSMF first
    (usually fails fast), then fall back to the same stack as live capture.
    """
    import cv2  # type: ignore

    if platform.system().lower().startswith("win"):
        for backend in (cv2.CAP_MSMF, cv2.CAP_DSHOW, None):
            cap = (
                cv2.VideoCapture(camera_index, backend)
                if backend is not None
                else cv2.VideoCapture(camera_index)
            )
            try:
                if not cap.isOpened():
                    continue
                ok, frame = cap.read()
                if ok and frame is not None and getattr(frame, "size", 0) > 0:
                    return True
            finally:
                cap.release()
        return False

    cap = open_video_capture(camera_index)
    try:
        if not cap.isOpened():
            return False
        ok, frame = cap.read()
        return bool(ok and frame is not None and getattr(frame, "size", 0) > 0)
    finally:
        cap.release()


def scan_camera_indices(max_index: int = 10) -> list[int]:
    """Return indices that open and return at least one frame (for GUI / scripting)."""
    configure_opencv_io_logging(quiet=True)
    try:
        found: list[int] = []
        for i in range(max_index):
            if probe_camera_readable(i):
                found.append(i)
        return found
    finally:
        configure_opencv_io_logging(quiet=False)


def run_list_cameras(max_index: int = 10) -> None:
    # flush=True so output appears immediately (no blank screen while probing).
    print("Scanning camera indices (opens each device briefly)...", flush=True)
    configure_opencv_io_logging(quiet=True)
    found: list[int] = []
    try:
        for i in range(max_index):
            print(f"  index {i} ...", end=" ", flush=True)
            if probe_camera_readable(i):
                print("OK", flush=True)
                found.append(i)
            else:
                print("-", flush=True)
    finally:
        configure_opencv_io_logging(quiet=False)
    if not found:
        print("No working cameras found. Check USB, privacy settings, and drivers.", flush=True)
        return
    print(
        f"Working camera_index value(s) for config.json: {', '.join(str(i) for i in found)}",
        flush=True,
    )
    print("Set sensor.camera_index to the index that shows your desk.", flush=True)


def _overlay_debug_cfg(config: dict[str, Any]) -> tuple[float, bool]:
    dbg = config.get("debug", {})
    scale = float(dbg.get("overlay_font_scale", 0.85))
    fullscreen = bool(dbg.get("fullscreen_overlay", False))
    return scale, fullscreen


def draw_accessible_hud(
    frame: np.ndarray,
    lines: list[str],
    *,
    footer: str,
    font_scale: float,
    margin: int = 14,
) -> None:
    """High-contrast top banner + footer so operators can read status at a glance."""
    import cv2  # type: ignore

    h, w = frame.shape[:2]
    line_h = int(32 * font_scale) + 8
    banner_h = margin * 2 + len(lines) * line_h + 6
    banner_h = min(banner_h, h // 2)

    roi = frame[0:banner_h, 0:w]
    tint = np.full_like(roi, (28, 28, 28))
    roi[:] = cv2.addWeighted(roi, 0.25, tint, 0.75, 0)

    y = margin + int(26 * font_scale)
    for line in lines:
        cv2.putText(
            frame,
            line,
            (margin, y),
            cv2.FONT_HERSHEY_DUPLEX,
            font_scale,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        y += line_h

    # Footer strip along bottom
    fh = int(36 * font_scale) + margin
    y0 = max(0, h - fh)
    froi = frame[y0:h, 0:w]
    ftint = np.full_like(froi, (22, 22, 22))
    froi[:] = cv2.addWeighted(froi, 0.35, ftint, 0.65, 0)
    cv2.putText(
        frame,
        footer,
        (margin, h - margin - 4),
        cv2.FONT_HERSHEY_DUPLEX,
        font_scale * 0.95,
        (180, 255, 180),
        2,
        cv2.LINE_AA,
    )


def draw_mode_badge(frame: np.ndarray, *, dry_run: bool, font_scale: float) -> None:
    """Top-right corner: obvious DRY vs LIVE indicator."""
    import cv2  # type: ignore

    h, w = frame.shape[:2]
    text = "  DRY RUN (no HTTP)  " if dry_run else "  LIVE -> VPS  "
    fs = max(0.45, font_scale * 0.55)
    thick = 2
    (tw, th), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_DUPLEX, fs, thick)
    pad = 10
    x1, y1 = w - pad, int(36 * font_scale) + th + pad
    x0 = x1 - tw - pad * 2
    y0 = y1 - th - pad - baseline
    color = (0, 165, 255) if dry_run else (60, 200, 80)
    cv2.rectangle(frame, (x0, y0), (x1, y1), color, -1)
    cv2.rectangle(frame, (x0, y0), (x1, y1), (255, 255, 255), 1)
    cv2.putText(
        frame,
        text,
        (x0 + pad // 2, y1 - pad // 2 - baseline),
        cv2.FONT_HERSHEY_DUPLEX,
        fs,
        (255, 255, 255),
        thick,
        cv2.LINE_AA,
    )


def draw_operator_legend(frame: np.ndarray, font_scale: float) -> None:
    """Right-side cheat sheet for deliberate gestures (MVP)."""
    import cv2  # type: ignore

    h, w = frame.shape[:2]
    lines = [
        "GESTURES",
        "Arms up (shoulders)",
        "  arm_execute",
        "Two-hand pinch",
        "  confirm_execute",
        "Open palm",
        "  pause",
        "Cross wrists",
        "  cancel",
    ]
    fs = max(0.38, font_scale * 0.48)
    line_h = int(20 * fs) + 5
    margin = 10
    panel_w = min(int(260 * max(1.0, font_scale / 0.85)), w // 3)
    panel_h = margin * 2 + len(lines) * line_h
    x0 = max(0, w - panel_w - margin)
    footer_reserve = int(42 * font_scale) + 28
    y0 = h - panel_h - footer_reserve - margin
    y0 = max(margin, y0)

    roi = frame[y0 : y0 + panel_h, x0 : w - margin]
    if roi.size == 0:
        return
    tint = np.full_like(roi, (20, 24, 20))
    roi[:] = cv2.addWeighted(roi, 0.35, tint, 0.65, 0)

    y = y0 + margin + int(18 * fs)
    for i, line in enumerate(lines):
        col = (200, 255, 120) if i == 0 else (235, 235, 235)
        cv2.putText(
            frame,
            line,
            (x0 + margin, y),
            cv2.FONT_HERSHEY_DUPLEX,
            fs,
            col,
            1,
            cv2.LINE_AA,
        )
        y += line_h


def run_camera_preview(config: dict[str, Any], *, fullscreen: bool) -> None:
    """Show live desk cam only (no MediaPipe, no network) for hardware and framing checks."""
    import cv2  # type: ignore

    sensor_cfg = config["sensor"]
    camera_index = int(sensor_cfg.get("camera_index", 0))
    mirror = bool(sensor_cfg.get("mirror_preview", True))
    cap = open_video_capture(camera_index)
    if not cap.isOpened():
        print(
            f"Could not open camera_index={camera_index}. "
            f"Run: python sensor_engine.py --list-cameras",
            file=sys.stderr,
        )
        raise SystemExit(1)

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(sensor_cfg.get("frame_width", 640)))
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(sensor_cfg.get("frame_height", 480)))

    win = "Desk cam — preview (no AI, no network)"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    if fullscreen:
        cv2.setWindowProperty(win, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    print(
        "[PREVIEW] Live video only. Adjust config sensor.camera_index if this is the wrong device.\n"
        "[PREVIEW] Close the window or press Q / Esc to exit."
    )
    stale = 0
    while True:
        ok, frame = cap.read()
        if not ok or frame is None or frame.size == 0:
            stale += 1
            if stale > 50:
                print("[PREVIEW] No frames from camera; exiting.", file=sys.stderr)
                break
            time.sleep(0.05)
            continue
        stale = 0
        if mirror:
            frame = cv2.flip(frame, 1)

        font_scale, _ = _overlay_debug_cfg(config)
        draw_accessible_hud(
            frame,
            [
                "GESTURECONTROLENGINE — camera preview",
                f"Camera index {camera_index} — LIVE",
                "No pose/hands; no data sent to VPS",
            ],
            footer="Q or Esc = quit   |   Use --debug-overlay for AI + overlay",
            font_scale=font_scale,
        )
        cv2.imshow(win, frame)
        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), 27):
            break

    cap.release()
    cv2.destroyAllWindows()


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
    print(f"[TEST_MODE] emitting scripted events cycles={cycles}", flush=True)
    for _ in range(cycles):
        for event_type, confidence, payload in script:
            client.emit(event_type, confidence, payload)
            time.sleep(sleep_seconds)


def run_camera_loop(
    config: dict[str, Any],
    client: EventClient,
    debug_overlay: bool,
    *,
    fullscreen: bool = False,
) -> None:
    try:
        import cv2  # type: ignore
        import mediapipe as mp  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependencies for camera mode. Install requirements first."
        ) from exc

    if not hasattr(mp, "solutions"):
        raise RuntimeError(
            "Installed mediapipe is missing the legacy Solutions API (mp.solutions). "
            "Use a compatible version, e.g. pip install -r requirements.txt "
            "(mediapipe>=0.10.13,<0.10.31)."
        )

    sensor_cfg = config["sensor"]
    presence = PresenceDetector(config.get("presence", {}))
    gesture = GestureDetector(config.get("gesture", {}))
    machine = ArmStateMachine(
        confirm_window_seconds=float(config.get("policy", {}).get("confirm_window_seconds", 8.0))
    )

    camera_index = int(sensor_cfg.get("camera_index", 0))
    cap = open_video_capture(camera_index)

    if not cap.isOpened():
        raise RuntimeError(
            f"Could not open camera index {camera_index}. "
            "Try python sensor_engine.py --list-cameras and set sensor.camera_index in config.json."
        )

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(sensor_cfg.get("frame_width", 640)))
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(sensor_cfg.get("frame_height", 480)))

    mp_pose = mp.solutions.pose
    mp_hands = mp.solutions.hands
    mp_drawing = mp.solutions.drawing_utils

    last_heartbeat = 0.0
    heartbeat_seconds = float(config["sensor"].get("heartbeat_seconds", 15))
    mirror = bool(sensor_cfg.get("mirror_preview", True))
    draw_landmarks = bool(config.get("debug", {}).get("draw_landmarks", False))
    show_operator_legend = bool(config.get("debug", {}).get("show_operator_legend", True))
    font_scale, cfg_fullscreen = _overlay_debug_cfg(config)
    use_fullscreen = fullscreen or cfg_fullscreen

    win_title = "Desk cam — sensor + AI (local preview)"
    if debug_overlay:
        cv2.namedWindow(win_title, cv2.WINDOW_NORMAL)
        if use_fullscreen:
            cv2.setWindowProperty(win_title, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    fps_ema = 0.0
    t_prev = time.perf_counter()
    last_gesture_hud = "Last gesture: —"

    with mp_pose.Pose(
        min_detection_confidence=float(sensor_cfg.get("pose_detection_confidence", 0.5)),
        min_tracking_confidence=float(sensor_cfg.get("pose_tracking_confidence", 0.5)),
    ) as pose_model, mp_hands.Hands(
        max_num_hands=int(sensor_cfg.get("max_num_hands", 2)),
        min_detection_confidence=float(sensor_cfg.get("hand_detection_confidence", 0.5)),
        min_tracking_confidence=float(sensor_cfg.get("hand_tracking_confidence", 0.5)),
    ) as hands_model:
        print(
            "[INFO] camera loop started — "
            + ("fullscreen " if (debug_overlay and use_fullscreen) else "")
            + "press Q or Esc in the video window to quit",
            flush=True,
        )
        if debug_overlay:
            print("=" * 58, flush=True)
            print(" Console shows [DRY_RUN] / [SEND_OK] lines as events fire (unbuffered: python -u).", flush=True)
            print(f" Video window: {win_title!r} — HUD shows FPS, pose, hands, sequence, gestures.", flush=True)
            print("=" * 58, flush=True)
        while True:
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.05)
                continue

            t_now = time.perf_counter()
            dt = t_now - t_prev
            t_prev = t_now
            inst = 1.0 / dt if dt > 1e-6 else 0.0
            fps_ema = inst if fps_ema <= 0 else 0.85 * fps_ema + 0.15 * inst

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
                print(
                    f"[PRESENCE] state={transition.state} confidence={transition.confidence}",
                    flush=True,
                )
                client.emit(
                    "presence.state_changed",
                    transition.confidence,
                    {"state": transition.state},
                )

            gestures = gesture.update(now, pose_result, hands_result)
            for item in gestures:
                print(
                    f"[GESTURE] gesture={item.gesture} confidence={item.confidence}",
                    flush=True,
                )
                last_gesture_hud = f"Last gesture: {item.gesture} ({item.confidence:.0%})"
                client.emit(
                    "gesture.detected",
                    item.confidence,
                    {"gesture": item.gesture},
                )
                sm_transition = machine.on_gesture(item.gesture, now)
                if sm_transition:
                    print(f"[STATE_MACHINE] {sm_transition}", flush=True)

            timeout_transition = machine.on_tick(now)
            if timeout_transition:
                print(f"[STATE_MACHINE] {timeout_transition}", flush=True)

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
                send_mode = "DRY-RUN (events not sent)" if client.dry_run else "Sending events to VPS"
                pose_ok = bool(pose_result.pose_landmarks)
                n_hands = len(hands_result.multi_hand_landmarks or [])
                hb_left = max(0.0, heartbeat_seconds - (now - last_heartbeat))
                hud_lines = [
                    "GESTURECONTROLENGINE — desk sensor",
                    f"Camera {camera_index} | {send_mode}",
                    client.vps_link_line,
                    f"FPS ~{fps_ema:.0f}  |  Pose: {'YES' if pose_ok else 'no'}  |  Hands: {n_hands}",
                    f"Sequence: {client.last_sequence}  |  Heartbeat in ~{int(hb_left)}s",
                    f"Presence: {presence.state}  |  Arm: {machine.state}",
                    last_gesture_hud,
                    f"Sensor ID: {sensor_cfg.get('sensor_id')}",
                    "Video stays on this PC — only metadata is sent",
                ]
                lm = "ON" if draw_landmarks else "OFF (config debug.draw_landmarks)"
                draw_accessible_hud(
                    frame,
                    hud_lines,
                    footer=f"Landmarks {lm}  |  Q or Esc = quit",
                    font_scale=font_scale,
                )
                draw_mode_badge(frame, dry_run=client.dry_run, font_scale=font_scale)
                if show_operator_legend:
                    draw_operator_legend(frame, font_scale)

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

                cv2.imshow(win_title, frame)
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
        "--camera-preview",
        action="store_true",
        help="Live camera only (no MediaPipe, no network) to verify desk cam and framing",
    )
    parser.add_argument(
        "--list-cameras",
        action="store_true",
        help="Print camera_index values that open successfully (configure sensor.camera_index)",
    )
    parser.add_argument(
        "--fullscreen",
        action="store_true",
        help="With --debug-overlay or --camera-preview, use a fullscreen window",
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
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Open the Windows Tkinter control panel (camera scan, preview, launch sensor)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.gui:
        from desktop_gui import main as gui_main

        gui_main()
        return

    cfg_path = Path(args.config)
    config = load_config(str(cfg_path))

    if args.list_cameras:
        run_list_cameras()
        return

    if args.camera_preview:
        run_camera_preview(config, fullscreen=args.fullscreen)
        return

    client = build_client(config, dry_run=args.dry_run)

    if args.test_events:
        run_test_events(client, cycles=args.test_cycles, sleep_seconds=args.test_sleep_seconds)
        return

    run_camera_loop(
        config,
        client,
        debug_overlay=args.debug_overlay,
        fullscreen=args.fullscreen,
    )


if __name__ == "__main__":
    main()
