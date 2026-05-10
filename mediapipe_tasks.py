"""MediaPipe Tasks API (Pose + Hands) with legacy-compatible result objects.

The gesture/presence modules expect objects shaped like the old Solutions API:
  pose_result.pose_landmarks.landmark[i].{x,y,z,visibility}
  hands_result.multi_hand_landmarks[k].landmark[i].{x,y,z}
"""

from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

import numpy as np

# Official task model bundles (float16 / lite for edge performance)
POSE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    "pose_landmarker_lite/float16/latest/pose_landmarker_lite.task"
)
HAND_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/latest/hand_landmarker.task"
)

POSE_MODEL_FILENAME = "pose_landmarker_lite.task"
HAND_MODEL_FILENAME = "hand_landmarker.task"

# BlazePose 33-landmark topology (same indices as legacy pose)
POSE_CONNECTIONS: frozenset[tuple[int, int]] = frozenset(
    [
        (0, 1),
        (1, 2),
        (2, 3),
        (3, 7),
        (0, 4),
        (4, 5),
        (5, 6),
        (6, 8),
        (9, 10),
        (11, 12),
        (11, 13),
        (13, 15),
        (15, 17),
        (15, 19),
        (15, 21),
        (17, 19),
        (12, 14),
        (14, 16),
        (16, 18),
        (16, 20),
        (16, 22),
        (18, 20),
        (11, 23),
        (12, 24),
        (23, 24),
        (23, 25),
        (25, 27),
        (27, 29),
        (29, 31),
        (27, 31),
        (24, 26),
        (26, 28),
        (28, 30),
        (30, 32),
        (28, 32),
    ]
)

HAND_CONNECTIONS: frozenset[tuple[int, int]] = frozenset(
    [
        (0, 1),
        (1, 2),
        (2, 3),
        (3, 4),
        (0, 5),
        (5, 6),
        (6, 7),
        (7, 8),
        (0, 9),
        (9, 10),
        (10, 11),
        (11, 12),
        (0, 13),
        (13, 14),
        (14, 15),
        (15, 16),
        (0, 17),
        (17, 18),
        (18, 19),
        (19, 20),
    ]
)


def default_model_dir() -> Path:
    return Path(__file__).resolve().parent / "models"


def ensure_task_models(model_dir: Path) -> tuple[Path, Path]:
    """Download pose + hand task files if missing. Returns (pose_path, hand_path)."""
    model_dir.mkdir(parents=True, exist_ok=True)
    pose_path = model_dir / POSE_MODEL_FILENAME
    hand_path = model_dir / HAND_MODEL_FILENAME

    if not pose_path.is_file():
        print(f"[mediapipe] Downloading pose model -> {pose_path}", flush=True)
        urllib.request.urlretrieve(POSE_MODEL_URL, pose_path)

    if not hand_path.is_file():
        print(f"[mediapipe] Downloading hand model -> {hand_path}", flush=True)
        urllib.request.urlretrieve(HAND_MODEL_URL, hand_path)

    return pose_path, hand_path


@dataclass
class _LM:
    x: float
    y: float
    z: float
    visibility: float = 1.0


class _LandmarkList:
    __slots__ = ("landmark",)

    def __init__(self, landmark: list[_LM]) -> None:
        self.landmark = landmark


class _PoseResult:
    __slots__ = ("pose_landmarks",)

    def __init__(self, pose_landmarks: Optional[_LandmarkList]) -> None:
        self.pose_landmarks = pose_landmarks


class _HandsResult:
    __slots__ = ("multi_hand_landmarks",)

    def __init__(self, multi_hand_landmarks: list[_LandmarkList]) -> None:
        self.multi_hand_landmarks = multi_hand_landmarks


def _pose_proto_to_list(landmarks: Any) -> list[_LM]:
    out: list[_LM] = []
    for lm in landmarks:
        vis = float(getattr(lm, "visibility", 1.0) or 1.0)
        out.append(_LM(float(lm.x), float(lm.y), float(lm.z), vis))
    return out


def _hand_proto_to_list(landmarks: Any) -> list[_LM]:
    out: list[_LM] = []
    for lm in landmarks:
        out.append(
            _LM(
                float(lm.x),
                float(lm.y),
                float(lm.z),
                float(getattr(lm, "visibility", 1.0) or 1.0),
            )
        )
    return out


def _import_tasks() -> tuple[Any, Any, Any, Any, Any, Any]:
    import mediapipe as mp

    try:
        from mediapipe.tasks import python as mp_tasks_python
        from mediapipe.tasks.python import vision as mp_tasks_vision
    except ImportError as exc:
        raise RuntimeError(
            "MediaPipe Tasks API not found. Install a recent mediapipe: pip install -r requirements.txt"
        ) from exc

    BaseOptions = mp_tasks_python.BaseOptions
    return (
        mp,
        BaseOptions,
        mp_tasks_vision.PoseLandmarker,
        mp_tasks_vision.PoseLandmarkerOptions,
        mp_tasks_vision.HandLandmarker,
        mp_tasks_vision.HandLandmarkerOptions,
    )


class MediaPipeTasksVision:
    """VIDEO-mode Pose + Hand landmarkers; exposes legacy-shaped results per frame."""

    def __init__(self, sensor_cfg: dict[str, Any], mp_cfg: Optional[dict[str, Any]] = None) -> None:
        mp_cfg = mp_cfg or {}
        model_dir = Path(mp_cfg.get("model_dir", default_model_dir()))
        if not model_dir.is_absolute():
            model_dir = (Path.cwd() / model_dir).resolve()

        pose_name = str(mp_cfg.get("pose_model", POSE_MODEL_FILENAME))
        hand_name = str(mp_cfg.get("hand_model", HAND_MODEL_FILENAME))
        pose_path = model_dir / pose_name
        hand_path = model_dir / hand_name

        if not pose_path.is_file() or not hand_path.is_file():
            d_pose, d_hand = ensure_task_models(model_dir)
            if not pose_path.is_file():
                pose_path = d_pose
            if not hand_path.is_file():
                hand_path = d_hand

        (
            self._mp,
            BaseOptions,
            PoseLandmarker,
            PoseLandmarkerOptions,
            HandLandmarker,
            HandLandmarkerOptions,
        ) = _import_tasks()

        RunningMode = self._mp.tasks.vision.RunningMode

        p_det = float(sensor_cfg.get("pose_detection_confidence", 0.5))
        p_trk = float(sensor_cfg.get("pose_tracking_confidence", 0.5))
        h_det = float(sensor_cfg.get("hand_detection_confidence", 0.5))
        h_trk = float(sensor_cfg.get("hand_tracking_confidence", 0.5))
        max_hands = int(sensor_cfg.get("max_num_hands", 2))

        pose_opts = PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(pose_path)),
            running_mode=RunningMode.VIDEO,
            min_pose_detection_confidence=p_det,
            min_pose_presence_confidence=p_trk,
            min_tracking_confidence=p_trk,
            num_poses=1,
            output_segmentation_masks=False,
        )
        hand_opts = HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(hand_path)),
            running_mode=RunningMode.VIDEO,
            num_hands=max_hands,
            min_hand_detection_confidence=h_det,
            min_hand_presence_confidence=h_trk,
            min_tracking_confidence=h_trk,
        )

        self._pose = PoseLandmarker.create_from_options(pose_opts)
        self._hands = HandLandmarker.create_from_options(hand_opts)
        self._ts_ms = 0

    def __enter__(self) -> MediaPipeTasksVision:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self._pose.close()
        self._hands.close()

    def process(self, rgb_uint8: np.ndarray) -> tuple[_PoseResult, _HandsResult]:
        """rgb_uint8: HxWx3 RGB. Returns legacy-shaped pose/hands wrappers."""
        self._ts_ms += 33
        if not rgb_uint8.flags.c_contiguous:
            rgb_uint8 = np.ascontiguousarray(rgb_uint8)
        mp_image = self._mp.Image(
            image_format=self._mp.ImageFormat.SRGB, data=rgb_uint8
        )

        pr = self._pose.detect_for_video(mp_image, self._ts_ms)
        hr = self._hands.detect_for_video(mp_image, self._ts_ms)

        pose_ll: Optional[_LandmarkList] = None
        if pr.pose_landmarks and len(pr.pose_landmarks) > 0:
            pose_ll = _LandmarkList(_pose_proto_to_list(pr.pose_landmarks[0]))

        hand_lists: list[_LandmarkList] = []
        if hr.hand_landmarks:
            for hl in hr.hand_landmarks:
                hand_lists.append(_LandmarkList(_hand_proto_to_list(hl)))

        return _PoseResult(pose_ll), _HandsResult(hand_lists)


def draw_pose_connections(
    frame_bgr: np.ndarray,
    pose_landmarks: Optional[_LandmarkList],
    *,
    line_color: tuple[int, int, int] = (0, 255, 0),
    line_thickness: int = 2,
) -> None:
    import cv2

    if not pose_landmarks or not pose_landmarks.landmark:
        return
    h, w = frame_bgr.shape[:2]
    lms = pose_landmarks.landmark

    def pt(i: int) -> tuple[int, int]:
        lm = lms[i]
        return int(float(lm.x) * w), int(float(lm.y) * h)

    for a, b in POSE_CONNECTIONS:
        if a < len(lms) and b < len(lms):
            cv2.line(frame_bgr, pt(a), pt(b), line_color, line_thickness, cv2.LINE_AA)


def draw_hand_connections(
    frame_bgr: np.ndarray,
    hand_landmarks: _LandmarkList,
    *,
    line_color: tuple[int, int, int] = (255, 200, 0),
    line_thickness: int = 2,
) -> None:
    import cv2

    if not hand_landmarks.landmark:
        return
    h, w = frame_bgr.shape[:2]
    lms = hand_landmarks.landmark

    def pt(i: int) -> tuple[int, int]:
        lm = lms[i]
        return int(float(lm.x) * w), int(float(lm.y) * h)

    for a, b in HAND_CONNECTIONS:
        if a < len(lms) and b < len(lms):
            cv2.line(frame_bgr, pt(a), pt(b), line_color, line_thickness, cv2.LINE_AA)


def draw_tasks_landmarks(
    frame_bgr: np.ndarray,
    pose_result: _PoseResult,
    hands_result: _HandsResult,
    *,
    draw_pose: bool,
    draw_hands: bool,
) -> None:
    if draw_pose and pose_result.pose_landmarks:
        draw_pose_connections(frame_bgr, pose_result.pose_landmarks)
    if draw_hands and hands_result.multi_hand_landmarks:
        for hand in hands_result.multi_hand_landmarks:
            draw_hand_connections(frame_bgr, hand)
