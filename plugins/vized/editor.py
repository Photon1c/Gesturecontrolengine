"""Vized — fullscreen geometric editor overlaid on the camera feed."""

from __future__ import annotations

import time
from typing import Any

from .gestures import GestureInterpreter
from .persistence import autosave_if_due, load_scene, save_scene
from .renderer import SceneRenderer
from .scene import PRIMITIVE_KINDS, Scene3D


MODES = ("select", "create", "move", "rotate")


class VizedEditor:
    name = "vized"

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        gesture_cfg = cfg.get("gestures") if isinstance(cfg.get("gestures"), dict) else {}
        scene_cfg = cfg.get("scene") if isinstance(cfg.get("scene"), dict) else {}
        self._gestures = GestureInterpreter(gesture_cfg)
        self._renderer = SceneRenderer(cfg)
        self._scene = Scene3D()
        self._mode = str(cfg.get("default_mode", "create"))
        self._primitive = str(scene_cfg.get("default_primitive", "box"))
        palette = scene_cfg.get("palette") or [
            [0, 210, 255],
            [0, 180, 120],
            [255, 160, 0],
            [220, 90, 255],
        ]
        self._palette = [tuple(int(c) for c in row[:3]) for row in palette]
        self._palette_index = 0
        self._show_grid = bool(scene_cfg.get("show_grid", True))
        self._cursor_world: tuple[float, float, float] | None = None
        self._cursor_px: tuple[int, int] | None = None
        self._dragging = False
        self._rotate_active = False
        self._prev_fist = False
        self._status = "Ready"
        self._last_autosave = time.time()
        self._depth_offset = float(
            (cfg.get("projection") or {}).get("default_depth", 0.0)
        )
        self._load_startup_scene()

    def _load_startup_scene(self) -> None:
        persist = self.cfg.get("persistence") if isinstance(self.cfg.get("persistence"), dict) else {}
        startup = persist.get("startup_scene")
        if startup:
            try:
                self._scene = load_scene(str(startup))
                self._status = f"Loaded {startup}"
            except OSError:
                pass

    @property
    def scene(self) -> Scene3D:
        return self._scene

    def system_prompt(self) -> str:
        return (
            "Vized — geometric 3D editor on camera (fullscreen).\n"
            "Modes: S select | C create | M move | R rotate | Tab cycle selection\n"
            "Primitives: 1 box | 2 sphere | 3 line | 4 plane | 5 point | P palette\n"
            "Gestures: pinch = place/confirm | point + move = drag | fist = delete\n"
            "Open palm = select mode | two-hand spread = scale selected\n"
            "G grid | +/- depth | W save | O load | Del delete | Q quit"
        )

    def hud_line(self) -> str:
        sel = self._scene.selected()
        sel_label = sel.kind if sel else "none"
        return (
            f"{self._mode.upper()} · {self._primitive} · sel:{sel_label} · "
            f"n={len(self._scene.shapes)} · {self._status}"
        )

    def handle_key(self, key: int) -> str | None:
        if key in (ord("s"), ord("S")):
            self._mode = "select"
            self._status = "Select mode"
            return "mode:select"
        if key in (ord("c"), ord("C")):
            self._mode = "create"
            self._status = "Create mode — pinch to place"
            return "mode:create"
        if key in (ord("m"), ord("M")):
            self._mode = "move"
            self._status = "Move mode — point and pinch-drag"
            return "mode:move"
        if key in (ord("r"), ord("R")):
            self._mode = "rotate"
            self._status = "Rotate mode — pinch and move"
            return "mode:rotate"
        if key == 9:  # Tab
            self._scene.cycle_selection(1)
            self._status = "Cycled selection"
            return "select:next"
        if key in (8, 127, ord("x"), ord("X")):
            if self._scene.remove_selected():
                self._status = "Deleted shape"
                return "delete"
        if key in (ord("g"), ord("G")):
            self._show_grid = not self._show_grid
            self._renderer.show_grid = self._show_grid
            self._status = f"Grid {'on' if self._show_grid else 'off'}"
            return "grid:toggle"
        if key in (ord("p"), ord("P")):
            self._palette_index = (self._palette_index + 1) % len(self._palette)
            self._status = f"Palette {self._palette_index + 1}/{len(self._palette)}"
            return "palette"
        if key in (ord("+"), ord("=")):
            self._depth_offset = min(2.5, self._depth_offset + 0.12)
            self._status = f"Depth {self._depth_offset:+.2f}"
            return "depth:+"
        if key in (ord("-"), ord("_")):
            self._depth_offset = max(-1.5, self._depth_offset - 0.12)
            self._status = f"Depth {self._depth_offset:+.2f}"
            return "depth:-"
        if key in (ord("1"), ord("2"), ord("3"), ord("4"), ord("5")):
            idx = key - ord("1")
            if idx < len(PRIMITIVE_KINDS):
                self._primitive = PRIMITIVE_KINDS[idx]
                self._status = f"Primitive: {self._primitive}"
                return f"primitive:{self._primitive}"

        persist = self.cfg.get("persistence") if isinstance(self.cfg.get("persistence"), dict) else {}
        if key in (ord("w"), ord("W")):
            path = str(persist.get("manual_save_path", "vized_scenes/scene.json"))
            save_scene(self._scene, path)
            self._status = f"Saved {path}"
            return "save"
        if key in (ord("o"), ord("O")):
            path = str(persist.get("manual_save_path", "vized_scenes/scene.json"))
            try:
                self._scene = load_scene(path)
                self._status = f"Loaded {path}"
                return "load"
            except OSError as exc:
                self._status = f"Load failed: {exc}"
        return None

    def handle_key_char(self, char: str, *, ctrl: bool = False) -> str | None:
        if not char:
            return None
        c = char.lower()
        mapping = {
            "s": "select",
            "c": "create",
            "m": "move",
            "r": "rotate",
            "g": "grid",
            "p": "palette",
            "x": "delete",
        }
        if ctrl and c == "s":
            persist = self.cfg.get("persistence") if isinstance(self.cfg.get("persistence"), dict) else {}
            path = str(persist.get("manual_save_path", "vized_scenes/scene.json"))
            save_scene(self._scene, path)
            self._status = f"Saved {path}"
            return "save"
        if ctrl and c == "l":
            persist = self.cfg.get("persistence") if isinstance(self.cfg.get("persistence"), dict) else {}
            path = str(persist.get("manual_save_path", "vized_scenes/scene.json"))
            try:
                self._scene = load_scene(path)
                self._status = f"Loaded {path}"
                return "load"
            except OSError as exc:
                self._status = f"Load failed: {exc}"
                return None
        if c in mapping and not ctrl:
            return self.handle_key(ord(c if c.isupper() else c))
        if c.isdigit() and c in "12345":
            return self.handle_key(ord(c))
        if c in "+=":
            return self.handle_key(ord(c))
        if c in "-_":
            return self.handle_key(ord(c))
        return None

    def update(
        self,
        frame_w: int,
        frame_h: int,
        hands_result: Any,
        *,
        mirror: bool,
        dt: float,
    ) -> dict[str, Any]:
        now = time.time()
        gestures = self._gestures.update(hands_result, mirror=mirror, now=now)
        hand = gestures.hands[0] if gestures.hands else None

        if hand:
            nx, ny, _ = hand.cursor_norm
            self._cursor_px = self._renderer.norm_to_pixel(nx, ny, frame_w=frame_w, frame_h=frame_h)
            desk_z = float(
                (self.cfg.get("projection") or {}).get("desk_plane_z", 0.0)
            )
            depth_z = desk_z + self._depth_offset
            self._cursor_world = self._renderer.norm_to_world(
                nx, ny, frame_w=frame_w, frame_h=frame_h, depth_z=depth_z
            )
        else:
            self._cursor_px = None

        if hand and hand.open_palm and not hand.pinch:
            if self._mode != "select":
                self._mode = "select"
                self._status = "Open palm → select mode"

        if hand and hand.fist:
            if not self._prev_fist:
                if self._scene.remove_selected():
                    self._status = "Fist delete"
            self._prev_fist = True
        else:
            self._prev_fist = False

        if gestures.two_hand_spread_delta and abs(gestures.two_hand_spread_delta) > 0.008:
            sel = self._scene.selected()
            if sel:
                factor = 1.0 + gestures.two_hand_spread_delta * 2.2
                sx, sy, sz = sel.scale
                sel.scale = (
                    max(0.05, sx * factor),
                    max(0.05, sy * factor),
                    max(0.05, sz * factor),
                )
                self._status = "Two-hand scale"

        if self._mode == "create" and gestures.pinch_down and self._cursor_world:
            color = self._palette[self._palette_index % len(self._palette)]
            self._scene.new_shape(
                self._primitive,
                self._cursor_world,
                color=color,
            )
            self._status = f"Placed {self._primitive}"

        elif self._mode == "select" and gestures.pinch_down and self._cursor_world:
            picked = self._pick_at_cursor(frame_w, frame_h)
            if picked:
                self._scene.selected_id = picked
                self._status = f"Selected {picked}"
            else:
                self._scene.cycle_selection(1)
                self._status = "Cycle selection"

        elif self._mode == "move":
            if hand and hand.pointing and hand.pinch:
                self._dragging = True
            if self._dragging and self._cursor_world:
                sel = self._scene.selected()
                if sel:
                    sel.position = self._cursor_world
                    self._status = "Moving selection"
            if gestures.pinch_up:
                self._dragging = False

        elif self._mode == "rotate":
            if hand and hand.pinch:
                self._rotate_active = True
            if self._rotate_active and hand:
                sel = self._scene.selected()
                if sel:
                    rx, ry, rz = sel.rotation
                    sel.rotation = (
                        rx,
                        ry + hand.cursor_norm[0] * 120 * dt,
                        rz + hand.cursor_norm[1] * 120 * dt,
                    )
                    self._status = "Rotating selection"
            if gestures.pinch_up:
                self._rotate_active = False

        self._last_autosave = autosave_if_due(
            self._scene,
            self.cfg,
            now=now,
            last_save=self._last_autosave,
        )

        return {
            "mode": self._mode,
            "primitive": self._primitive,
            "shape_count": len(self._scene.shapes),
            "selected_id": self._scene.selected_id,
            "cursor_world": self._cursor_world,
            "gesture": {
                "pinch": hand.pinch if hand else False,
                "open_palm": hand.open_palm if hand else False,
                "fist": hand.fist if hand else False,
                "pointing": hand.pointing if hand else False,
            },
        }

    def _pick_at_cursor(self, frame_w: int, frame_h: int) -> str | None:
        if not self._cursor_px:
            return None
        cx, cy = self._cursor_px
        best_id: str | None = None
        best_dist = 9999.0
        for shape in self._scene.shapes:
            pt = self._renderer.projection.project(
                shape.position, frame_w=frame_w, frame_h=frame_h
            )
            if not pt:
                continue
            d = ((pt[0] - cx) ** 2 + (pt[1] - cy) ** 2) ** 0.5
            if d < best_dist and d < 48:
                best_dist = d
                best_id = shape.id
        return best_id

    def render(self, frame: Any) -> None:
        self._renderer.draw(
            frame,
            self._scene,
            cursor_px=self._cursor_px,
            cursor_world=self._cursor_world if self._mode in ("create", "move") else None,
        )

    def close(self) -> None:
        persist = self.cfg.get("persistence") if isinstance(self.cfg.get("persistence"), dict) else {}
        path = str(persist.get("autosave_path", "vized_scenes/last_scene.json"))
        try:
            save_scene(self._scene, path)
        except OSError:
            pass
