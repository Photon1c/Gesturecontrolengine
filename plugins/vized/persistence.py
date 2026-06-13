"""Save and load Vized scenes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .scene import Scene3D


def save_scene(scene: Scene3D, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(scene.to_dict(), indent=2), encoding="utf-8")


def load_scene(path: str | Path) -> Scene3D:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Scene file must be a JSON object")
    return Scene3D.from_dict(raw)


def autosave_if_due(
    scene: Scene3D,
    cfg: dict[str, Any],
    *,
    now: float,
    last_save: float,
) -> float:
    persist = cfg.get("persistence") if isinstance(cfg.get("persistence"), dict) else {}
    interval = float(persist.get("autosave_seconds", 30))
    path = str(persist.get("autosave_path", "vized_scenes/last_scene.json"))
    if interval <= 0 or now - last_save < interval:
        return last_save
    save_scene(scene, path)
    return now
