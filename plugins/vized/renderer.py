"""OpenCV renderer for projected 3D geometry on the camera frame."""

from __future__ import annotations

from typing import Any

import numpy as np

from .projection import CameraProjection
from .scene import Scene3D, Shape3D, line_endpoints, shape_corners, shape_wireframe_edges


class SceneRenderer:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        proj_cfg = cfg.get("projection") if isinstance(cfg.get("projection"), dict) else {}
        self.projection = CameraProjection.from_config(proj_cfg)
        scene_cfg = cfg.get("scene") if isinstance(cfg.get("scene"), dict) else {}
        self.show_grid = bool(scene_cfg.get("show_grid", True))
        self.grid_size = float(scene_cfg.get("grid_size", 2.0))
        self.grid_divisions = int(scene_cfg.get("grid_divisions", 8))
        self.grid_z = float(scene_cfg.get("grid_z", 0.0))

    def draw(
        self,
        frame: np.ndarray,
        scene: Scene3D,
        *,
        cursor_px: tuple[int, int] | None = None,
        cursor_world: tuple[float, float, float] | None = None,
    ) -> None:
        import cv2  # type: ignore

        h, w = frame.shape[:2]
        overlay = frame.copy()
        if self.show_grid:
            self._draw_grid(overlay, w, h, cv2)

        for shape in scene.shapes:
            selected = shape.id == scene.selected_id
            self._draw_shape(overlay, shape, selected=selected, w=w, h=h, cv2=cv2)

        if cursor_world is not None:
            self._draw_cursor_anchor(overlay, cursor_world, w, h, cv2)

        if cursor_px is not None:
            cv2.circle(frame, cursor_px, 10, (255, 255, 255), 1, lineType=cv2.LINE_AA)
            cv2.circle(frame, cursor_px, 3, (0, 255, 255), -1, lineType=cv2.LINE_AA)

        alpha = float(self.cfg.get("overlay_alpha", 0.72))
        cv2.addWeighted(overlay, alpha, frame, 1.0 - alpha, 0, frame)

    def _draw_grid(self, frame: np.ndarray, w: int, h: int, cv2: Any) -> None:
        pts = self.projection.grid_points(
            size=self.grid_size,
            divisions=self.grid_divisions,
            z=self.grid_z,
        )
        color = (70, 90, 110)
        for i in range(0, len(pts), 2):
            if i + 1 >= len(pts):
                break
            p0 = self.projection.project(pts[i], frame_w=w, frame_h=h)
            p1 = self.projection.project(pts[i + 1], frame_w=w, frame_h=h)
            if p0 and p1:
                cv2.line(frame, p0, p1, color, 1, lineType=cv2.LINE_AA)

    def _draw_shape(
        self,
        frame: np.ndarray,
        shape: Shape3D,
        *,
        selected: bool,
        w: int,
        h: int,
        cv2: Any,
    ) -> None:
        color = shape.color
        if selected:
            color = (min(255, color[0] + 40), min(255, color[1] + 40), 255)

        if shape.kind == "sphere":
            center = self.projection.project(shape.position, frame_w=w, frame_h=h)
            if center is None:
                return
            radius = self.projection.projected_radius(
                shape.position, shape.scale[0] * 0.5, frame_w=w, frame_h=h
            )
            cv2.circle(frame, center, radius, color, 1, lineType=cv2.LINE_AA)
            if selected:
                cv2.circle(frame, center, radius + 3, (255, 255, 255), 1, lineType=cv2.LINE_AA)
            return

        if shape.kind == "line":
            a, b = line_endpoints(shape)
            pa = self.projection.project(a, frame_w=w, frame_h=h)
            pb = self.projection.project(b, frame_w=w, frame_h=h)
            if pa and pb:
                cv2.line(frame, pa, pb, color, 2, lineType=cv2.LINE_AA)
            return

        if shape.kind == "point":
            p = self.projection.project(shape.position, frame_w=w, frame_h=h)
            if p:
                cv2.circle(frame, p, 6, color, -1, lineType=cv2.LINE_AA)
            return

        corners = shape_corners(shape)
        projected: list[tuple[int, int] | None] = [
            self.projection.project(c, frame_w=w, frame_h=h) for c in corners
        ]
        for i, j in shape_wireframe_edges(shape):
            if i >= len(projected) or j >= len(projected):
                continue
            p0, p1 = projected[i], projected[j]
            if p0 and p1:
                cv2.line(frame, p0, p1, color, 2 if selected else 1, lineType=cv2.LINE_AA)

    def _draw_cursor_anchor(
        self,
        frame: np.ndarray,
        world: tuple[float, float, float],
        w: int,
        h: int,
        cv2: Any,
    ) -> None:
        px = self.projection.project(world, frame_w=w, frame_h=h)
        if not px:
            return
        size = 14
        cv2.line(frame, (px[0] - size, px[1]), (px[0] + size, px[1]), (0, 255, 255), 1)
        cv2.line(frame, (px[0], px[1] - size), (px[0], px[1] + size), (0, 255, 255), 1)
        depth = self.projection.camera_z - world[2]
        label = f"z={world[2]:+.2f} d={depth:.1f}"
        cv2.putText(
            frame,
            label,
            (px[0] + 10, px[1] - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (220, 220, 220),
            1,
            cv2.LINE_AA,
        )

    def norm_to_pixel(
        self, nx: float, ny: float, *, frame_w: int, frame_h: int
    ) -> tuple[int, int]:
        return int(round(nx * frame_w)), int(round(ny * frame_h))

    def norm_to_world(
        self,
        nx: float,
        ny: float,
        *,
        frame_w: int,
        frame_h: int,
        depth_z: float | None = None,
    ) -> tuple[float, float, float]:
        return self.projection.unproject_norm(
            nx, ny, frame_w=frame_w, frame_h=frame_h, depth_z=depth_z
        )
