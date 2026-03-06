"""Presence detector with stable state transitions for edge sensing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
import math


# MediaPipe pose landmark indices (PoseLandmark enum values).
NOSE = 0
LEFT_SHOULDER = 11
RIGHT_SHOULDER = 12
LEFT_WRIST = 15
RIGHT_WRIST = 16
LEFT_HIP = 23
RIGHT_HIP = 24


@dataclass
class PresenceTransition:
    state: str
    confidence: float


class PresenceDetector:
    """Debounced presence state detector.

    State set for MVP:
      - at_terminal
      - away
      - resting
      - asleep
      - unknown
    """

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        self.current_state = "unknown"
        self._raw_state = "unknown"
        self._raw_state_since: Optional[float] = None
        self._last_points: Optional[list[tuple[float, float]]] = None
        self._last_visibility: float = 0.0

        self._stable_secs = {
            "at_terminal": float(cfg.get("at_terminal_stable_seconds", 12.0)),
            "away": float(cfg.get("away_stable_seconds", 45.0)),
            "resting": float(cfg.get("resting_stable_seconds", 90.0)),
            "asleep": float(cfg.get("asleep_stable_seconds", 180.0)),
            "unknown": float(cfg.get("unknown_stable_seconds", 8.0)),
        }
        self._transition_debounce_seconds = float(
            cfg.get("transition_debounce_seconds", 1.5)
        )
        self._min_visibility = float(cfg.get("min_pose_visibility", 0.5))
        self._resting_motion = float(cfg.get("resting_motion_threshold", 0.006))
        self._asleep_motion = float(cfg.get("asleep_motion_threshold", 0.003))
        self._head_down_threshold = float(cfg.get("head_down_threshold", -0.02))
        self._desk_zone = cfg.get(
            "desk_zone",
            {
                "x_min": 0.2,
                "x_max": 0.8,
                "y_min": 0.05,
                "y_max": 0.9,
            },
        )

    @property
    def state(self) -> str:
        return self.current_state

    def update(self, ts: float, pose_result: Any, hands_result: Any) -> Optional[PresenceTransition]:
        raw_state, confidence = self._classify_frame(pose_result, hands_result)

        if self._raw_state != raw_state:
            self._raw_state = raw_state
            self._raw_state_since = ts
        elif self._raw_state_since is None:
            self._raw_state_since = ts

        if raw_state == self.current_state:
            return None

        stable_seconds = self._stable_secs.get(raw_state, self._stable_secs["unknown"])
        stable_for = ts - (self._raw_state_since or ts)

        if stable_for + 1e-6 < (stable_seconds + self._transition_debounce_seconds):
            return None

        self.current_state = raw_state
        return PresenceTransition(state=raw_state, confidence=confidence)

    def _classify_frame(self, pose_result: Any, hands_result: Any) -> tuple[str, float]:
        pose_landmarks = getattr(pose_result, "pose_landmarks", None)
        hand_landmarks = getattr(hands_result, "multi_hand_landmarks", None) or []

        visibility_mode = self._visibility_mode(pose_landmarks, hand_landmarks)
        motion = self._estimate_motion(pose_landmarks)
        head_down = self._is_head_down(pose_landmarks)

        if visibility_mode == "absent":
            return "away", self._away_confidence()
        if visibility_mode == "ambiguous":
            return "unknown", round(0.6 + self._last_visibility * 0.2, 3)

        if motion is not None and motion <= self._asleep_motion and head_down:
            return "asleep", self._asleep_confidence(motion)

        if motion is not None and motion <= self._resting_motion:
            return "resting", self._resting_confidence(motion)

        return "at_terminal", self._at_terminal_confidence()

    def _visibility_mode(self, pose_landmarks: Any, hand_landmarks: list[Any]) -> str:
        if pose_landmarks and getattr(pose_landmarks, "landmark", None):
            lms = pose_landmarks.landmark
            keypoints = [NOSE, LEFT_SHOULDER, RIGHT_SHOULDER]
            vis_values = [float(lms[idx].visibility) for idx in keypoints if idx < len(lms)]
            if vis_values:
                self._last_visibility = sum(vis_values) / len(vis_values)
                if self._last_visibility >= self._min_visibility:
                    if NOSE < len(lms) and self._in_desk_zone(lms[NOSE]):
                        return "present"
                    return "ambiguous"
                return "ambiguous"

        # Hands-only fallback lets "at_terminal" survive temporary pose loss.
        if hand_landmarks:
            self._last_visibility = max(self._last_visibility, 0.7)
            return "present"

        self._last_visibility = max(0.0, self._last_visibility * 0.8)
        return "absent"

    def _in_desk_zone(self, nose: Any) -> bool:
        return (
            self._desk_zone["x_min"] <= float(nose.x) <= self._desk_zone["x_max"]
            and self._desk_zone["y_min"] <= float(nose.y) <= self._desk_zone["y_max"]
        )

    def _estimate_motion(self, pose_landmarks: Any) -> Optional[float]:
        if not pose_landmarks or not getattr(pose_landmarks, "landmark", None):
            self._last_points = None
            return None

        lms = pose_landmarks.landmark
        indices = [NOSE, LEFT_SHOULDER, RIGHT_SHOULDER, LEFT_HIP, RIGHT_HIP, LEFT_WRIST, RIGHT_WRIST]
        points = [(float(lms[i].x), float(lms[i].y)) for i in indices if i < len(lms)]

        if not points:
            self._last_points = None
            return None

        if self._last_points is None or len(self._last_points) != len(points):
            self._last_points = points
            return None

        deltas = []
        for (x1, y1), (x2, y2) in zip(points, self._last_points):
            deltas.append(math.hypot(x1 - x2, y1 - y2))
        self._last_points = points
        return sum(deltas) / len(deltas)

    def _is_head_down(self, pose_landmarks: Any) -> bool:
        if not pose_landmarks or not getattr(pose_landmarks, "landmark", None):
            return False
        lms = pose_landmarks.landmark
        if max(NOSE, LEFT_SHOULDER, RIGHT_SHOULDER) >= len(lms):
            return False
        nose_y = float(lms[NOSE].y)
        shoulder_mid_y = (float(lms[LEFT_SHOULDER].y) + float(lms[RIGHT_SHOULDER].y)) / 2.0
        # Normal posture is typically nose_y < shoulder_mid_y. Higher ratio means head dropped.
        return (nose_y - shoulder_mid_y) >= self._head_down_threshold

    def _away_confidence(self) -> float:
        return round(min(1.0, 0.7 + (1.0 - self._last_visibility) * 0.3), 3)

    def _at_terminal_confidence(self) -> float:
        return round(min(1.0, 0.65 + self._last_visibility * 0.35), 3)

    def _resting_confidence(self, motion: float) -> float:
        ratio = 1.0 - min(1.0, motion / max(self._resting_motion, 1e-6))
        return round(0.72 + ratio * 0.24, 3)

    def _asleep_confidence(self, motion: float) -> float:
        ratio = 1.0 - min(1.0, motion / max(self._asleep_motion, 1e-6))
        return round(0.8 + ratio * 0.18, 3)
