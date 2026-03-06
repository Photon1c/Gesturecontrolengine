"""Gesture detector with debounce and cooldown for deliberate commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import math


# MediaPipe pose indices
LEFT_SHOULDER = 11
RIGHT_SHOULDER = 12
LEFT_WRIST = 15
RIGHT_WRIST = 16

# MediaPipe hand indices
WRIST = 0
THUMB_TIP = 4
THUMB_IP = 3
INDEX_TIP = 8
INDEX_PIP = 6
MIDDLE_TIP = 12
MIDDLE_PIP = 10
RING_TIP = 16
RING_PIP = 14
PINKY_TIP = 20
PINKY_PIP = 18


@dataclass
class GestureEvent:
    gesture: str
    confidence: float


class GestureDetector:
    """Detects deliberate gestures with frame-level debounce and cooldown."""

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        self._streaks = {
            "arm_execute": 0,
            "confirm_execute": 0,
            "pause": 0,
            "cancel": 0,
        }
        self._last_emit_ts = {
            "arm_execute": 0.0,
            "confirm_execute": 0.0,
            "pause": 0.0,
            "cancel": 0.0,
        }

    def update(self, ts: float, pose_result: Any, hands_result: Any) -> list[GestureEvent]:
        pose_landmarks = getattr(pose_result, "pose_landmarks", None)
        hand_landmarks = getattr(hands_result, "multi_hand_landmarks", None) or []

        candidates = self._candidate_scores(pose_landmarks, hand_landmarks)
        output: list[GestureEvent] = []

        for gesture, score in candidates.items():
            enabled = score > 0.0
            if enabled:
                self._streaks[gesture] += 1
            else:
                self._streaks[gesture] = 0

            min_frames = int(self._gesture_cfg(gesture).get("min_hold_frames", 8))
            cooldown = float(self._gesture_cfg(gesture).get("cooldown_seconds", 4.0))
            on_cooldown = (ts - self._last_emit_ts[gesture]) < cooldown

            if self._streaks[gesture] >= min_frames and not on_cooldown:
                self._last_emit_ts[gesture] = ts
                self._streaks[gesture] = 0
                output.append(GestureEvent(gesture=gesture, confidence=round(score, 3)))

        return output

    def _gesture_cfg(self, name: str) -> dict[str, Any]:
        return self.cfg.get(name, {})

    def _candidate_scores(self, pose_landmarks: Any, hand_landmarks: list[Any]) -> dict[str, float]:
        return {
            "arm_execute": self._arm_execute_score(pose_landmarks),
            "confirm_execute": self._confirm_execute_score(hand_landmarks),
            "pause": self._pause_score(hand_landmarks),
            "cancel": self._cancel_score(pose_landmarks),
        }

    def _arm_execute_score(self, pose_landmarks: Any) -> float:
        if not pose_landmarks or not getattr(pose_landmarks, "landmark", None):
            return 0.0
        lms = pose_landmarks.landmark
        if max(LEFT_SHOULDER, RIGHT_SHOULDER, LEFT_WRIST, RIGHT_WRIST) >= len(lms):
            return 0.0

        margin = float(self._gesture_cfg("arm_execute").get("raise_margin", 0.05))
        left_diff = float(lms[LEFT_SHOULDER].y) - float(lms[LEFT_WRIST].y)
        right_diff = float(lms[RIGHT_SHOULDER].y) - float(lms[RIGHT_WRIST].y)
        if left_diff < margin or right_diff < margin:
            return 0.0
        normalized = min(1.0, min(left_diff, right_diff) / max(margin * 2.0, 1e-6))
        return 0.72 + normalized * 0.26

    def _confirm_execute_score(self, hand_landmarks: list[Any]) -> float:
        # Deliberate confirm requires two-hand pinch to avoid accidental fire.
        if len(hand_landmarks) < 2:
            return 0.0

        pinch_threshold = float(
            self._gesture_cfg("confirm_execute").get("pinch_distance_threshold", 0.05)
        )
        pinch_scores = []
        for hand in hand_landmarks[:2]:
            lms = hand.landmark
            d = self._distance(lms[THUMB_TIP], lms[INDEX_TIP])
            if d > pinch_threshold:
                return 0.0
            pinch_scores.append(1.0 - min(1.0, d / max(pinch_threshold, 1e-6)))

        if not pinch_scores:
            return 0.0
        return 0.78 + (sum(pinch_scores) / len(pinch_scores)) * 0.2

    def _pause_score(self, hand_landmarks: list[Any]) -> float:
        if not hand_landmarks:
            return 0.0
        for hand in hand_landmarks:
            score = self._open_palm_score(hand.landmark)
            if score > 0.0:
                return 0.7 + score * 0.25
        return 0.0

    def _cancel_score(self, pose_landmarks: Any) -> float:
        if not pose_landmarks or not getattr(pose_landmarks, "landmark", None):
            return 0.0
        lms = pose_landmarks.landmark
        if max(LEFT_WRIST, RIGHT_WRIST, LEFT_SHOULDER, RIGHT_SHOULDER) >= len(lms):
            return 0.0

        cross_x = float(self._gesture_cfg("cancel").get("cross_x_threshold", 0.08))
        cross_y = float(self._gesture_cfg("cancel").get("cross_y_threshold", 0.08))
        lw = lms[LEFT_WRIST]
        rw = lms[RIGHT_WRIST]

        dx = abs(float(lw.x) - float(rw.x))
        dy = abs(float(lw.y) - float(rw.y))
        if dx > cross_x or dy > cross_y:
            return 0.0

        shoulder_mid_y = (float(lms[LEFT_SHOULDER].y) + float(lms[RIGHT_SHOULDER].y)) / 2.0
        wrist_mid_y = (float(lw.y) + float(rw.y)) / 2.0
        if abs(wrist_mid_y - shoulder_mid_y) > 0.22:
            return 0.0

        tightness = 1.0 - min(1.0, (dx + dy) / max(cross_x + cross_y, 1e-6))
        return 0.74 + tightness * 0.22

    @staticmethod
    def _distance(p1: Any, p2: Any) -> float:
        return math.hypot(float(p1.x) - float(p2.x), float(p1.y) - float(p2.y))

    def _open_palm_score(self, lms: list[Any]) -> float:
        fingers = [
            (INDEX_TIP, INDEX_PIP),
            (MIDDLE_TIP, MIDDLE_PIP),
            (RING_TIP, RING_PIP),
            (PINKY_TIP, PINKY_PIP),
        ]
        wrist = lms[WRIST]
        extended = 0
        strength = 0.0
        for tip_i, pip_i in fingers:
            tip_dist = self._distance(lms[tip_i], wrist)
            pip_dist = self._distance(lms[pip_i], wrist)
            if tip_dist > pip_dist:
                extended += 1
                strength += min(1.0, (tip_dist - pip_dist) / 0.08)

        thumb_tip_dist = self._distance(lms[THUMB_TIP], wrist)
        thumb_ip_dist = self._distance(lms[THUMB_IP], wrist)
        thumb_open = thumb_tip_dist > thumb_ip_dist

        if extended >= 3 and thumb_open:
            return min(1.0, (strength / max(extended, 1)))
        return 0.0
