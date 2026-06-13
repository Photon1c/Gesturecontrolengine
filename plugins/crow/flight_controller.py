"""Body-to-bird control surface: wrist velocity, wingspan, and arm attitude."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

# MediaPipe pose indices
LEFT_SHOULDER = 11
RIGHT_SHOULDER = 12
LEFT_ELBOW = 13
RIGHT_ELBOW = 14
LEFT_WRIST = 15
RIGHT_WRIST = 16


@dataclass
class FlightControls:
    """Normalized control signals mapped from pose each frame."""

    flap_power: float = 0.0
    wingspan: float = 0.0
    bank_steering: float = 0.0
    pitch: float = 0.0
    glide: bool = False
    perch: bool = False
    tracking: bool = False
    autonomous: bool = False


@dataclass
class FlightDebug:
    """Optional tracking diagnostics — not shown on main HUD."""

    raw_left_wrist_y: float = 0.0
    raw_right_wrist_y: float = 0.0
    left_wrist_vy: float = 0.0
    right_wrist_vy: float = 0.0
    tracking_confidence: float = 0.0


class FlightController:
    """Detect flapping via periodic vertical wrist velocity, not static poses."""

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        self._prev_left_y: float | None = None
        self._prev_right_y: float | None = None
        self._prev_ts: float | None = None
        self._left_vy_smooth = 0.0
        self._right_vy_smooth = 0.0
        self._flap_ema = 0.0
        self._bank_ema = 0.0
        self._pitch_ema = 0.0
        self._glide_active = False
        self._last_debug = FlightDebug()

    def debug_snapshot(self) -> dict[str, float]:
        d = self._last_debug
        return {
            "raw_left_wrist_y": round(d.raw_left_wrist_y, 4),
            "raw_right_wrist_y": round(d.raw_right_wrist_y, 4),
            "left_wrist_vy": round(d.left_wrist_vy, 4),
            "right_wrist_vy": round(d.right_wrist_vy, 4),
            "tracking_confidence": round(d.tracking_confidence, 4),
        }

    def update(self, ts: float, pose_result: Any) -> FlightControls:
        pose_landmarks = getattr(pose_result, "pose_landmarks", None)
        if not pose_landmarks or not getattr(pose_landmarks, "landmark", None):
            self._reset_tracking()
            self._last_debug = FlightDebug()
            return FlightControls()

        lms = pose_landmarks.landmark
        if max(LEFT_SHOULDER, RIGHT_SHOULDER, LEFT_WRIST, RIGHT_WRIST) >= len(lms):
            self._reset_tracking()
            self._last_debug = FlightDebug()
            return FlightControls()

        left = lms[LEFT_WRIST]
        right = lms[RIGHT_WRIST]
        left_sh = lms[LEFT_SHOULDER]
        right_sh = lms[RIGHT_SHOULDER]

        left_y = float(left.y)
        right_y = float(right.y)
        wingspan = self._distance(left, right)

        left_vy = 0.0
        right_vy = 0.0
        if self._prev_ts is not None and self._prev_left_y is not None:
            dt = max(ts - self._prev_ts, 1e-3)
            left_vy = (left_y - self._prev_left_y) / dt
            right_vy = (right_y - self._prev_right_y) / dt

        self._prev_left_y = left_y
        self._prev_right_y = right_y
        self._prev_ts = ts

        self._left_vy_smooth = self._smooth_vy(self._left_vy_smooth, left_vy)
        self._right_vy_smooth = self._smooth_vy(self._right_vy_smooth, right_vy)

        # Downstroke produces positive vy in image space (y increases downward).
        raw_flap = max(0.0, self._left_vy_smooth + self._right_vy_smooth)
        self._flap_ema = self._smooth_asymmetric(self._flap_ema, raw_flap)
        flap_power = min(
            1.0,
            self._flap_ema / max(float(self.cfg.get("flap_normalize", 2.0)), 1e-6),
        )
        # Reward confident downstrokes without punishing light input as harshly.
        curve = float(self.cfg.get("flap_response_curve", 0.88))
        flap_power = min(1.0, flap_power**curve * float(self.cfg.get("flap_response_gain", 1.08)))

        wrist_bank = (left_y - right_y) * float(self.cfg.get("bank_wrist_gain", 7.5))
        shoulder_bank = (float(left_sh.y) - float(right_sh.y)) * float(
            self.cfg.get("bank_shoulder_gain", 5.0)
        )
        lean_bank = (float(left.x) - float(right.x)) * float(
            self.cfg.get("bank_lean_gain", 4.0)
        )
        raw_bank = (
            wrist_bank * float(self.cfg.get("bank_wrist_weight", 0.55))
            + shoulder_bank * float(self.cfg.get("bank_shoulder_weight", 0.3))
            + lean_bank * float(self.cfg.get("bank_lean_weight", 0.15))
        ) * float(self.cfg.get("bank_gain", 1.15))
        raw_bank = max(-1.0, min(1.0, raw_bank))
        bank_deadzone = float(self.cfg.get("bank_deadzone", 0.045))
        if abs(raw_bank) < bank_deadzone:
            raw_bank = 0.0
        self._bank_ema = self._smooth_bank(self._bank_ema, raw_bank)

        shoulder_z = (float(left_sh.z) + float(right_sh.z)) / 2.0
        wrist_z = (float(left.z) + float(right.z)) / 2.0
        raw_pitch = shoulder_z - wrist_z
        self._pitch_ema = self._smooth_pitch(self._pitch_ema, raw_pitch)
        pitch = max(-1.0, min(1.0, self._pitch_ema * float(self.cfg.get("pitch_gain", 3.6))))

        glide_enter = float(self.cfg.get("glide_wingspan_threshold", 0.52))
        glide_exit = float(self.cfg.get("glide_wingspan_exit_threshold", 0.40))
        if self._glide_active:
            self._glide_active = wingspan >= glide_exit
        else:
            self._glide_active = wingspan >= glide_enter
        glide = self._glide_active

        perch_span = float(self.cfg.get("perch_wingspan_threshold", 0.18))
        perch_pull = float(self.cfg.get("perch_pullback_threshold", 0.09))
        wrist_shoulder_dist = (
            self._distance(left, left_sh) + self._distance(right, right_sh)
        ) / 2.0
        perch = (
            wingspan <= perch_span
            and wrist_shoulder_dist <= perch_pull
            and flap_power < 0.05
            and abs(self._bank_ema) < 0.06
        )

        tracking_confidence = self._tracking_confidence(lms)
        confidence_floor = float(self.cfg.get("tracking_confidence_floor", 0.55))
        confidence_scale = max(
            confidence_floor,
            min(1.0, (tracking_confidence - 0.25) / 0.65),
        )
        flap_power = min(1.0, flap_power * confidence_scale)
        bank_out = max(-1.0, min(1.0, self._bank_ema * confidence_scale))
        pitch_out = max(-1.0, min(1.0, pitch * confidence_scale))

        self._last_debug = FlightDebug(
            raw_left_wrist_y=left_y,
            raw_right_wrist_y=right_y,
            left_wrist_vy=self._left_vy_smooth,
            right_wrist_vy=self._right_vy_smooth,
            tracking_confidence=tracking_confidence,
        )

        return FlightControls(
            flap_power=round(flap_power, 4),
            wingspan=round(wingspan, 4),
            bank_steering=round(bank_out, 4),
            pitch=round(pitch_out, 4),
            glide=glide,
            perch=perch,
            tracking=True,
        )

    def _smooth_vy(self, prev: float, raw: float) -> float:
        attack = float(self.cfg.get("vy_attack", 0.42))
        decay = float(self.cfg.get("vy_decay", 0.14))
        alpha = attack if abs(raw) >= abs(prev) else decay
        return alpha * raw + (1.0 - alpha) * prev

    def _smooth_asymmetric(self, prev: float, raw: float) -> float:
        attack = float(self.cfg.get("flap_attack", 0.52))
        decay = float(self.cfg.get("flap_decay", 0.10))
        alpha = attack if raw >= prev else decay
        return alpha * raw + (1.0 - alpha) * prev

    def _smooth_bank(self, prev: float, raw: float) -> float:
        attack = float(self.cfg.get("bank_attack", 0.52))
        decay = float(self.cfg.get("bank_decay", 0.1))
        alpha = attack if abs(raw) >= abs(prev) else decay
        return alpha * raw + (1.0 - alpha) * prev

    def _smooth_pitch(self, prev: float, raw: float) -> float:
        attack = float(self.cfg.get("pitch_attack", 0.45))
        decay = float(self.cfg.get("pitch_decay", 0.12))
        alpha = attack if abs(raw) >= abs(prev) else decay
        return alpha * raw + (1.0 - alpha) * prev

    def _tracking_confidence(self, lms: list[Any]) -> float:
        indices = (LEFT_WRIST, RIGHT_WRIST, LEFT_SHOULDER, RIGHT_SHOULDER)
        vis = [
            float(getattr(lms[idx], "visibility", 1.0) or 1.0)
            for idx in indices
            if idx < len(lms)
        ]
        if not vis:
            return 0.0
        return sum(vis) / len(vis)

    def _reset_tracking(self) -> None:
        self._prev_left_y = None
        self._prev_right_y = None
        self._prev_ts = None
        self._left_vy_smooth = 0.0
        self._right_vy_smooth = 0.0
        self._glide_active = False

    @staticmethod
    def _distance(p1: Any, p2: Any) -> float:
        return math.hypot(
            float(p1.x) - float(p2.x),
            float(p1.y) - float(p2.y),
            float(p1.z) - float(p2.z),
        )
