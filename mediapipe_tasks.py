"""MediaPipe Tasks API (Pose + Hands) with legacy-compatible result objects.

The gesture/presence modules expect objects shaped like the old Solutions API:
  pose_result.pose_landmarks.landmark[i].{x,y,z,visibility}
  hands_result.multi_hand_landmarks[k].landmark[i].{x,y,z}
"""

from __future__ import annotations

import sys
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

_MEDIAPIPE_WINDOWS_NATIVE_HINT = """
MediaPipe Tasks could not load its native DLL correctly on this Python install.

Common cause: the venv was created with Anaconda/Miniconda. A folder named .venv or
.gestenv does not help — the *base* Python that created the venv must be python.org
CPython. Your traceback used Anaconda's ctypes (D:\\...\\Anaconda\\Lib\\ctypes).

Diagnostics:
  Interpreter: {exe}
  sys.prefix:  {prefix}
  pyvenv.cfg:  {pyvenv_hint}

Fix (do this exactly):
  1. Install 64-bit CPython 3.10 or 3.11 from https://www.python.org/downloads/
     (check "Add python.exe to PATH" or note the install path).
  2. List interpreters — the python.org line must NOT be under Anaconda:
       py -0p
  3. Create a NEW venv using the FULL path to python.org (example):
       & "$env:LOCALAPPDATA\\Programs\\Python\\Python311\\python.exe" -m venv .venv
     If that path does not exist, use the path shown by py -0p for "Python 3.11" etc.
  4. REMOVE old broken envs so you do not accidentally activate them:
       # optional: rmdir /s /q .gestenv
  5. Activate ONLY the new venv:
       .\\.venv\\Scripts\\activate
       python -c "import sys; print(sys.executable)"   # must be ...\\.venv\\Scripts\\python.exe
       pip install -U pip
       pip install -r requirements.txt
  6. Run: python sensor_engine.py --debug-overlay

Also install the latest "Microsoft Visual C++ Redistributable" if problems persist.
Run: python scripts/diagnose_mediapipe_env.py
"""


def _pyvenv_cfg_hint() -> str:
    cfg = Path(sys.prefix) / "pyvenv.cfg"
    if not cfg.is_file():
        return "(no pyvenv.cfg — not a venv, or unusual layout)"
    lines: list[str] = []
    for raw in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line.lower().startswith("home"):
            lines.append(line)
        elif line.lower().startswith("executable"):
            lines.append(line)
    if not lines:
        return str(cfg)
    return "; ".join(lines)


def _venv_home_is_conda() -> bool:
    cfg = Path(sys.prefix) / "pyvenv.cfg"
    if not cfg.is_file():
        return False
    text = cfg.read_text(encoding="utf-8", errors="replace").lower()
    for line in text.splitlines():
        ls = line.strip().lower()
        if ls.startswith("home ="):
            home = ls.split("=", 1)[1].strip()
            return any(x in home for x in ("conda", "anaconda", "miniconda"))
    return "conda" in text


def _is_likely_conda_python() -> bool:
    exe = sys.executable.lower()
    if any(x in exe for x in ("conda", "miniconda", "anaconda")):
        return True
    if "conda" in sys.prefix.lower():
        return True
    return _venv_home_is_conda()


def _wrap_mediapipe_native_errors(exc: BaseException) -> None:
    """Re-raise with a short operator hint when Tasks native bindings fail."""
    msg = str(exc).lower()
    detail = _MEDIAPIPE_WINDOWS_NATIVE_HINT.format(
        exe=sys.executable,
        prefix=sys.prefix,
        pyvenv_hint=_pyvenv_cfg_hint(),
    )
    if "free" in msg and "not found" in msg:
        raise RuntimeError(detail) from exc
    if _is_likely_conda_python() and isinstance(exc, (AttributeError, OSError)):
        raise RuntimeError(detail) from exc
    raise exc


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
    """VIDEO-mode Pose + Hand landmarkers; exposes legacy-shaped results per frame.

    Performance knobs (via sensor_cfg or mp_cfg):
      - inference_scale: fraction to downscale before ML (e.g. 0.5 = half-res). Default 0.5.
      - hand_skip_n: only run hand landmarker every N frames. Default 1 (every frame).
      - skip_hands_without_pose: skip hand detection when pose is absent. Default True.
    """

    def __init__(self, sensor_cfg: dict[str, Any], mp_cfg: Optional[dict[str, Any]] = None) -> None:
        import time as _time

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

        try:
            self._pose = PoseLandmarker.create_from_options(pose_opts)
            self._hands = HandLandmarker.create_from_options(hand_opts)
        except (AttributeError, OSError) as exc:
            _wrap_mediapipe_native_errors(exc)

        self._t0_ms = int(_time.perf_counter() * 1000)
        self._inference_scale = float(mp_cfg.get("inference_scale", 0.5))
        self._hand_skip_n = max(1, int(mp_cfg.get("hand_skip_n", 1)))
        self._skip_hands_without_pose = bool(mp_cfg.get("skip_hands_without_pose", True))
        self._frame_idx = 0
        self._last_hands = _HandsResult([])

    def __enter__(self) -> MediaPipeTasksVision:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self._pose.close()
        self._hands.close()

    def _downscale(self, rgb: np.ndarray) -> np.ndarray:
        s = self._inference_scale
        if s >= 0.99:
            return rgb
        import cv2
        h, w = rgb.shape[:2]
        new_w, new_h = max(1, int(w * s)), max(1, int(h * s))
        return cv2.resize(rgb, (new_w, new_h), interpolation=cv2.INTER_AREA)

    def process(self, rgb_uint8: np.ndarray) -> tuple[_PoseResult, _HandsResult]:
        """rgb_uint8: HxWx3 RGB at full camera resolution.

        Internally downscales for inference (normalized landmarks are scale-invariant).
        """
        import time as _time

        ts_ms = int(_time.perf_counter() * 1000) - self._t0_ms
        ts_ms = max(1, ts_ms)

        small = self._downscale(rgb_uint8)
        if not small.flags.c_contiguous:
            small = np.ascontiguousarray(small)

        mp_image = self._mp.Image(
            image_format=self._mp.ImageFormat.SRGB, data=small
        )

        pr = self._pose.detect_for_video(mp_image, ts_ms)
        pose_ll: Optional[_LandmarkList] = None
        if pr.pose_landmarks and len(pr.pose_landmarks) > 0:
            pose_ll = _LandmarkList(_pose_proto_to_list(pr.pose_landmarks[0]))

        self._frame_idx += 1
        run_hands = True
        if self._skip_hands_without_pose and pose_ll is None:
            run_hands = False
        if run_hands and self._hand_skip_n > 1 and (self._frame_idx % self._hand_skip_n) != 0:
            run_hands = False

        if run_hands:
            hr = self._hands.detect_for_video(mp_image, ts_ms)
            hand_lists: list[_LandmarkList] = []
            if hr.hand_landmarks:
                for hl in hr.hand_landmarks:
                    hand_lists.append(_LandmarkList(_hand_proto_to_list(hl)))
            self._last_hands = _HandsResult(hand_lists)

        return _PoseResult(pose_ll), self._last_hands


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
