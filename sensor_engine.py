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
from typing import Any, NamedTuple

# Before MediaPipe / TensorFlow load (Tasks runtime may pull TF).
os.environ.setdefault("TF_ENABLE_ONEDNN_OPTS", "0")

import numpy as np

from event_client import EventClient
from gesture_detector import GestureDetector
from mediapipe_tasks import MediaPipeTasksVision, draw_tasks_landmarks
from presence_detector import PresenceDetector

try:
    from plugins.jarvis.orchestrator import JarvisOrchestrator
    from plugins.jarvis.clap_detector import AudioClapDetector

    JARVIS_AVAILABLE = True
except ImportError:
    JARVIS_AVAILABLE = False


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
                if (
                    self.armed_since is not None
                    and (ts - self.armed_since) <= self.confirm_window_seconds
                ):
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
        print(
            "No working cameras found. Check USB, privacy settings, and drivers.",
            flush=True,
        )
        return
    print(
        f"Working camera_index value(s) for config.json: {', '.join(str(i) for i in found)}",
        flush=True,
    )
    print("Set sensor.camera_index to the index that shows your desk.", flush=True)


class OverlayUiConfig(NamedTuple):
    font_scale: float
    fullscreen: bool
    window_width: int
    window_height: int
    compact_hud: bool


def overlay_ui_config(config: dict[str, Any]) -> OverlayUiConfig:
    dbg = config.get("debug", {}) if isinstance(config.get("debug"), dict) else {}
    return OverlayUiConfig(
        font_scale=float(dbg.get("overlay_font_scale", 0.64)),
        fullscreen=bool(dbg.get("fullscreen_overlay", False)),
        window_width=int(dbg.get("default_window_width", 1280)),
        window_height=int(dbg.get("default_window_height", 720)),
        compact_hud=bool(dbg.get("compact_hud", True)),
    )


def _truncate_hud_line(text: str, max_chars: int) -> str:
    t = text.strip()
    if len(t) <= max_chars:
        return t
    if max_chars <= 3:
        return t[:max_chars]
    return t[: max_chars - 3] + "..."


def _resize_debug_window(win: str, ui: OverlayUiConfig, *, fullscreen: bool) -> None:
    """Open a comfortably large window; actual video resolution stays in the frame."""
    import cv2  # type: ignore

    if fullscreen:
        return
    w = max(480, ui.window_width)
    h = max(360, ui.window_height)
    try:
        cv2.resizeWindow(win, w, h)
    except Exception:
        pass


def _darken_roi(roi: np.ndarray, alpha: float = 0.25) -> None:
    """In-place darken using integer math — avoids np.full_like + addWeighted allocation."""
    np.multiply(roi, alpha, out=roi, casting="unsafe")


def draw_accessible_hud(
    frame: np.ndarray,
    lines: list[str],
    *,
    footer: str,
    font_scale: float,
    compact: bool = True,
) -> None:
    """High-contrast top banner + footer so operators can read status at a glance."""
    import cv2  # type: ignore

    h, w = frame.shape[:2]
    if compact:
        margin = 8
        line_h = int(20 * font_scale) + 4
        y0_text = margin + int(18 * font_scale)
        footer_fs = font_scale * 0.88
        thick = 1
        max_banner_frac = 0.28
        fh = int(24 * font_scale) + margin + 6
    else:
        margin = 14
        line_h = int(32 * font_scale) + 8
        y0_text = margin + int(26 * font_scale)
        footer_fs = font_scale * 0.95
        thick = 2
        max_banner_frac = 0.45
    banner_h = margin * 2 + len(lines) * line_h + 4
    banner_h = min(banner_h, int(h * max_banner_frac))

    _darken_roi(frame[0:banner_h, 0:w], 0.25)

    y = y0_text
    for line in lines:
        cv2.putText(
            frame,
            line,
            (margin, y),
            cv2.FONT_HERSHEY_DUPLEX,
            font_scale,
            (255, 255, 255),
            thick,
            cv2.LINE_AA,
        )
        y += line_h

    y0 = max(0, h - fh)
    _darken_roi(frame[y0:h, 0:w], 0.35)
    cv2.putText(
        frame,
        footer,
        (margin, h - margin - 3),
        cv2.FONT_HERSHEY_DUPLEX,
        footer_fs,
        (180, 255, 180),
        thick,
        cv2.LINE_AA,
    )


def draw_mode_badge(
    frame: np.ndarray, *, dry_run: bool, font_scale: float, compact: bool = True
) -> None:
    """Top-right corner: obvious DRY vs LIVE indicator."""
    import cv2  # type: ignore

    h, w = frame.shape[:2]
    text = "  DRY RUN (no HTTP)  " if dry_run else "  LIVE -> VPS  "
    mul = 0.48 if compact else 0.55
    fs = max(0.4, font_scale * mul)
    thick = 1 if compact else 2
    (tw, th), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_DUPLEX, fs, thick)
    pad = 8 if compact else 10
    top_y = int(26 * font_scale) if compact else int(36 * font_scale)
    x1, y1 = w - pad, top_y + th + pad
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


def draw_operator_legend(
    frame: np.ndarray, font_scale: float, *, compact: bool = True
) -> None:
    """Right-side cheat sheet for deliberate gestures (MVP)."""
    import cv2  # type: ignore

    h, w = frame.shape[:2]
    if compact:
        lines = [
            "GESTURES",
            "Arms up -> arm_execute",
            "Pinch -> confirm",
            "Open palm -> pause",
            "Cross wrists -> cancel",
        ]
    else:
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
    mul = 0.4 if compact else 0.48
    fs = max(0.32, font_scale * mul)
    line_h = int(15 * fs) + (3 if compact else 5)
    margin = 8 if compact else 10
    base_w = 168 if compact else 260
    panel_w = min(int(base_w * max(1.0, font_scale / 0.64)), w // 4 if compact else w // 3)
    panel_h = margin * 2 + len(lines) * line_h
    x0 = max(0, w - panel_w - margin)
    footer_reserve = int(28 * font_scale) + (18 if compact else 28)
    y0 = h - panel_h - footer_reserve - margin
    y0 = max(margin, y0)

    roi = frame[y0 : y0 + panel_h, x0 : w - margin]
    if roi.size == 0:
        return
    _darken_roi(roi, 0.35)

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
    ui = overlay_ui_config(config)
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    if fullscreen:
        cv2.setWindowProperty(win, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    else:
        _resize_debug_window(win, ui, fullscreen=False)

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

        if ui.compact_hud:
            hud_lines = [
                f"Preview | cam {camera_index} | no AI / no VPS",
                "Use --debug-overlay for sensor + overlay",
            ]
            footer = "Q/Esc quit"
        else:
            hud_lines = [
                "GESTURECONTROLENGINE — camera preview",
                f"Camera index {camera_index} — LIVE",
                "No pose/hands; no data sent to VPS",
            ]
            footer = "Q or Esc = quit   |   Use --debug-overlay for AI + overlay"
        draw_accessible_hud(
            frame,
            hud_lines,
            footer=footer,
            font_scale=ui.font_scale,
            compact=ui.compact_hud,
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
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependencies for camera mode. Install requirements first."
        ) from exc

    sensor_cfg = config["sensor"]
    presence = PresenceDetector(config.get("presence", {}))
    gesture = GestureDetector(config.get("gesture", {}))
    machine = ArmStateMachine(
        confirm_window_seconds=float(
            config.get("policy", {}).get("confirm_window_seconds", 8.0)
        )
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

    last_heartbeat = 0.0
    heartbeat_seconds = float(config["sensor"].get("heartbeat_seconds", 15))
    mirror = bool(sensor_cfg.get("mirror_preview", True))
    draw_landmarks = bool(config.get("debug", {}).get("draw_landmarks", False))
    show_operator_legend = bool(
        config.get("debug", {}).get("show_operator_legend", True)
    )
    ui = overlay_ui_config(config)
    font_scale = ui.font_scale
    use_fullscreen = fullscreen or ui.fullscreen

    win_title = "Desk cam — sensor + AI (local preview)"
    if debug_overlay:
        cv2.namedWindow(win_title, cv2.WINDOW_NORMAL)
        if use_fullscreen:
            cv2.setWindowProperty(
                win_title, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN
            )
        else:
            _resize_debug_window(win_title, ui, fullscreen=False)

    fps_ema = 0.0
    t_prev = time.perf_counter()
    last_gesture_hud = "Last gesture: —"

    mp_cfg = config.get("mediapipe") if isinstance(config.get("mediapipe"), dict) else None

    with MediaPipeTasksVision(sensor_cfg, mp_cfg) as vision_tasks:
        print(
            "[INFO] camera loop started — "
            + ("fullscreen " if (debug_overlay and use_fullscreen) else "")
            + "press Q or Esc in the video window to quit",
            flush=True,
        )
        print(
            "[INFO] MediaPipe Tasks (PoseLandmarker + HandLandmarker); "
            "models live under ./models/ (auto-download on first run).",
            flush=True,
        )
        if debug_overlay:
            print("=" * 58, flush=True)
            print(
                " Console shows [DRY_RUN] / [SEND_OK] lines as events fire (unbuffered: python -u).",
                flush=True,
            )
            print(
                f" Video window: {win_title!r} — HUD shows FPS, pose, hands, sequence, gestures.",
                flush=True,
            )
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
            pose_result, hands_result = vision_tasks.process(rgb)

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
                last_gesture_hud = (
                    f"Last gesture: {item.gesture} ({item.confidence:.0%})"
                )
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
                pose_ok = bool(pose_result.pose_landmarks)
                n_hands = len(hands_result.multi_hand_landmarks or [])
                hb_left = max(0.0, heartbeat_seconds - (now - last_heartbeat))
                fw = frame.shape[1]
                link_budget = max(42, fw // 8)
                link = _truncate_hud_line(client.vps_link_line, link_budget)
                gesture_line = last_gesture_hud
                if ui.compact_hud:
                    gesture_line = _truncate_hud_line(gesture_line, max(36, fw // 16))

                if ui.compact_hud:
                    send_tag = "DRY" if client.dry_run else "LIVE"
                    hud_lines = [
                        f"Desk sensor | cam{camera_index} | {send_tag} | seq {client.last_sequence}",
                        link,
                        f"FPS~{fps_ema:.0f} | pose:{'yes' if pose_ok else 'no'} | hands:{n_hands} | HB~{int(hb_left)}s",
                        f"{presence.state} | arm:{machine.state} | {gesture_line}",
                        f"ID {sensor_cfg.get('sensor_id')} | metadata only | LM {'on' if draw_landmarks else 'off'}",
                    ]
                    lm_short = "on" if draw_landmarks else "off"
                    footer = f"LM {lm_short} | Q/Esc quit"
                else:
                    send_mode = (
                        "DRY-RUN (events not sent)"
                        if client.dry_run
                        else "Sending events to VPS"
                    )
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
                    footer = f"Landmarks {lm}  |  Q or Esc = quit"

                draw_accessible_hud(
                    frame,
                    hud_lines,
                    footer=footer,
                    font_scale=font_scale,
                    compact=ui.compact_hud,
                )
                draw_mode_badge(
                    frame,
                    dry_run=client.dry_run,
                    font_scale=font_scale,
                    compact=ui.compact_hud,
                )
                if show_operator_legend:
                    draw_operator_legend(
                        frame, font_scale, compact=ui.compact_hud
                    )

                if draw_landmarks:
                    draw_tasks_landmarks(
                        frame,
                        pose_result,
                        hands_result,
                        draw_pose=True,
                        draw_hands=True,
                    )

                cv2.imshow(win_title, frame)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break

    cap.release()
    if debug_overlay:
        cv2.destroyAllWindows()


def run_jarvis_loop(config: dict[str, Any], jarvis_config_path: str) -> None:
    """JARVIS assistant mode: camera + gesture detection routed through JARVIS plugins."""
    if not JARVIS_AVAILABLE:
        print(
            "[JARVIS] plugins.jarvis not available. Ensure the plugins/jarvis/ directory exists.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    import cv2  # type: ignore

    sensor_cfg = config["sensor"]
    camera_index = int(sensor_cfg.get("camera_index", 0))
    mirror = bool(sensor_cfg.get("mirror_preview", True))

    cap = open_video_capture(camera_index)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open camera index {camera_index}.")

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(sensor_cfg.get("frame_width", 640)))
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(sensor_cfg.get("frame_height", 480)))

    gesture = GestureDetector(config.get("gesture", {}))
    jarvis_cfg = load_config(jarvis_config_path)
    orchestrator = JarvisOrchestrator(jarvis_cfg)
    clap_detector = (
        AudioClapDetector(jarvis_cfg.get("wakeup", {}))
        if jarvis_cfg.get("wakeup", {}).get("enabled", True)
        else None
    )

    ui = overlay_ui_config(config)
    font_scale = ui.font_scale
    win_title = "JARVIS — gesture + audio assistant"
    cv2.namedWindow(win_title, cv2.WINDOW_NORMAL)
    _resize_debug_window(win_title, ui, fullscreen=False)

    print("[JARVIS] Starting…", flush=True)
    print(orchestrator.system_prompt(), flush=True)
    print("=" * 58, flush=True)

    t_prev = time.perf_counter()
    fps_ema = 0.0

    mp_cfg = config.get("mediapipe") if isinstance(config.get("mediapipe"), dict) else None

    with MediaPipeTasksVision(sensor_cfg, mp_cfg) as vision_tasks:
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
            pose_result, hands_result = vision_tasks.process(rgb)

            now = time.time()
            vision_gestures = gesture.update(now, pose_result, hands_result)

            audio_gesture = None
            if clap_detector:
                audio_gesture = clap_detector.listen()

            all_outputs: list[str] = []
            for g in vision_gestures:
                outputs = orchestrator.route_gesture(g.gesture, g.confidence)
                all_outputs.extend(outputs)
                for o in outputs:
                    print(f"[JARVIS] {o}", flush=True)

            if audio_gesture:
                outputs = orchestrator.route_gesture(audio_gesture, 0.9)
                all_outputs.extend(outputs)
                for o in outputs:
                    print(f"[JARVIS][AUDIO] {o}", flush=True)

            tick_outputs = orchestrator.tick()
            for o in tick_outputs:
                print(f"[JARVIS] {o}", flush=True)
                all_outputs.append(o)

            pose_ok = bool(pose_result.pose_landmarks)
            n_hands = len(hands_result.multi_hand_landmarks or [])
            fw = frame.shape[1]
            if ui.compact_hud:
                tail = (
                    _truncate_hud_line(all_outputs[-1], max(36, fw // 9))
                    if all_outputs
                    else "Awaiting gesture/audio..."
                )
                hud_lines = [
                    f"JARVIS | cam{camera_index} | FPS~{fps_ema:.0f}",
                    f"pose:{'yes' if pose_ok else 'no'} | hands:{n_hands} | {tail}",
                    "arms | pinch | pause | cancel | clap",
                ]
                footer = "Q/Esc quit | JARVIS"
            else:
                hud_lines = [
                    "JARVIS — desk assistant",
                    f"Camera {camera_index} | FPS ~{fps_ema:.0f}",
                    f"Pose: {'YES' if pose_ok else 'no'}  |  Hands: {n_hands}",
                ]
                if all_outputs:
                    hud_lines.append(f"Last: {all_outputs[-1][:72]}")
                else:
                    hud_lines.append("Awaiting gesture or audio command...")
                hud_lines.append(
                    "Gestures: arm_execute | confirm | pause | cancel | clap (audio)"
                )
                footer = "Q or Esc = quit | JARVIS active"

            draw_accessible_hud(
                frame,
                hud_lines,
                footer=footer,
                font_scale=font_scale,
                compact=ui.compact_hud,
            )
            cv2.imshow(win_title, frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break

    if clap_detector:
        clap_detector.close()
    cap.release()
    cv2.destroyAllWindows()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="MediaPipe gesture + presence edge sensor"
    )
    parser.add_argument("--config", default="config.json", help="Path to config JSON")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print events but do not send"
    )
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
    parser.add_argument(
        "--jarvis",
        action="store_true",
        help="Activate JARVIS assistant mode (plugins: wakeup, atmosphere, devshop, project)",
    )
    parser.add_argument(
        "--jarvis-config",
        type=str,
        default="jarvis_config.json",
        help="Path to JARVIS plugin config JSON",
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

    if args.jarvis:
        run_jarvis_loop(config, args.jarvis_config)
        return

    client = build_client(config, dry_run=args.dry_run)

    if args.test_events:
        run_test_events(
            client, cycles=args.test_cycles, sleep_seconds=args.test_sleep_seconds
        )
        return

    run_camera_loop(
        config,
        client,
        debug_overlay=args.debug_overlay,
        fullscreen=args.fullscreen,
    )


if __name__ == "__main__":
    main()
