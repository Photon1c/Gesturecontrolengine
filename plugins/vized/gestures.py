"""Hand gesture interpretation for the Vized editor."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any


# MediaPipe hand landmark indices
WRIST = 0
THUMB_TIP = 4
INDEX_TIP = 8
MIDDLE_TIP = 12
RING_TIP = 16
PINKY_TIP = 20
INDEX_MCP = 5
MIDDLE_MCP = 9


@dataclass
class HandSample:
    handedness: str
    cursor_norm: tuple[float, float, float]
    pinch: bool
    open_palm: bool
    fist: bool
    pointing: bool
    spread: float


@dataclass
class GestureFrame:
    hands: list[HandSample]
    pinch_down: bool
    pinch_up: bool
    two_hand_spread: float | None
    two_hand_spread_delta: float


class GestureInterpreter:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        self._prev_pinch = False
        self._prev_spread: float | None = None
        self._last_pinch_time = 0.0
        self._cursor_smooth: tuple[float, float, float] | None = None

    def update(
        self,
        hands_result: Any,
        *,
        mirror: bool,
        now: float,
    ) -> GestureFrame:
        hands: list[HandSample] = []
        landmarks_list = hands_result.multi_hand_landmarks or []
        handedness_list = hands_result.multi_handedness or []

        for i, hand_lms in enumerate(landmarks_list):
            lms = hand_lms.landmark
            if len(lms) <= PINKY_TIP:
                continue
            label = "right"
            if i < len(handedness_list):
                entry = handedness_list[i]
                cat = getattr(entry, "classification", None)
                if cat:
                    label = str(getattr(cat[0], "label", "Right")).lower()

            nx = float(lms[INDEX_TIP].x)
            if mirror:
                nx = 1.0 - nx
            ny = float(lms[INDEX_TIP].y)
            nz = float(lms[INDEX_TIP].z)
            cursor = (nx, ny, nz)

            pinch_dist = self._distance(lms[THUMB_TIP], lms[INDEX_TIP])
            pinch_thr = float(self.cfg.get("pinch_threshold", 0.045))
            pinch = pinch_dist < pinch_thr

            spread = self._finger_spread(lms)
            open_thr = float(self.cfg.get("palm_open_threshold", 0.72))
            fist_thr = float(self.cfg.get("fist_threshold", 0.34))
            open_palm = spread >= open_thr
            fist = spread <= fist_thr
            pointing = self._is_pointing(lms) and not pinch

            hands.append(
                HandSample(
                    handedness=label,
                    cursor_norm=cursor,
                    pinch=pinch,
                    open_palm=open_palm,
                    fist=fist,
                    pointing=pointing,
                    spread=spread,
                )
            )

        dominant = self._pick_dominant(hands)
        if dominant:
            alpha = float(self.cfg.get("cursor_smoothing", 0.38))
            if self._cursor_smooth is None:
                self._cursor_smooth = dominant.cursor_norm
            else:
                cx, cy, cz = self._cursor_smooth
                dx, dy, dz = dominant.cursor_norm
                self._cursor_smooth = (
                    alpha * dx + (1 - alpha) * cx,
                    alpha * dy + (1 - alpha) * cy,
                    alpha * dz + (1 - alpha) * cz,
                )
            smoothed = HandSample(
                handedness=dominant.handedness,
                cursor_norm=self._cursor_smooth,
                pinch=dominant.pinch,
                open_palm=dominant.open_palm,
                fist=dominant.fist,
                pointing=dominant.pointing,
                spread=dominant.spread,
            )
            for i, hand in enumerate(hands):
                if hand.handedness == smoothed.handedness:
                    hands[i] = smoothed
                    break
            else:
                hands[0] = smoothed

        pinch_active = any(h.pinch for h in hands)
        cooldown = float(self.cfg.get("pinch_cooldown_ms", 350)) / 1000.0
        pinch_down = pinch_active and not self._prev_pinch
        pinch_up = (not pinch_active) and self._prev_pinch
        if pinch_down and now - self._last_pinch_time < cooldown:
            pinch_down = False
        if pinch_down:
            self._last_pinch_time = now
        self._prev_pinch = pinch_active

        two_spread = None
        spread_delta = 0.0
        if len(landmarks_list) >= 2 and len(landmarks_list[0].landmark) > INDEX_TIP:
            a = landmarks_list[0].landmark[INDEX_TIP]
            b = landmarks_list[1].landmark[INDEX_TIP]
            ax, bx = float(a.x), float(b.x)
            if mirror:
                ax, bx = 1.0 - ax, 1.0 - bx
            two_spread = math.hypot(ax - bx, float(a.y) - float(b.y))
            if self._prev_spread is not None:
                spread_delta = two_spread - self._prev_spread
            self._prev_spread = two_spread
        else:
            self._prev_spread = None

        return GestureFrame(
            hands=hands,
            pinch_down=pinch_down,
            pinch_up=pinch_up,
            two_hand_spread=two_spread,
            two_hand_spread_delta=spread_delta,
        )

    @staticmethod
    def _pick_dominant(hands: list[HandSample]) -> HandSample | None:
        if not hands:
            return None
        for hand in hands:
            if hand.handedness == "right":
                return hand
        return hands[0]

    @staticmethod
    def _distance(a: Any, b: Any) -> float:
        return math.hypot(
            float(a.x) - float(b.x),
            float(a.y) - float(b.y),
            float(a.z) - float(b.z),
        )

    @staticmethod
    def _finger_spread(lms: list[Any]) -> float:
        wrist = lms[WRIST]
        tips = (INDEX_TIP, MIDDLE_TIP, RING_TIP, PINKY_TIP)
        dists = [GestureInterpreter._distance(wrist, lms[i]) for i in tips]
        return sum(dists) / max(len(dists), 1)

    @staticmethod
    def _is_pointing(lms: list[Any]) -> bool:
        index_ext = GestureInterpreter._distance(lms[WRIST], lms[INDEX_TIP])
        middle_ext = GestureInterpreter._distance(lms[WRIST], lms[MIDDLE_TIP])
        return index_ext > middle_ext * 1.08 and index_ext > 0.12
