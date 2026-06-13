"""Pinhole projection from editor 3D space to screen pixels."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass
class CameraProjection:
    focal_length_px: float = 620.0
    camera_z: float = 4.5
    desk_plane_z: float = 0.0

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> CameraProjection:
        return cls(
            focal_length_px=float(cfg.get("focal_length_px", 620.0)),
            camera_z=float(cfg.get("camera_z", 4.5)),
            desk_plane_z=float(cfg.get("desk_plane_z", 0.0)),
        )

    def project(
        self,
        point: tuple[float, float, float],
        *,
        frame_w: int,
        frame_h: int,
    ) -> tuple[int, int] | None:
        x, y, z = point
        depth = self.camera_z - z
        if depth <= 0.05:
            return None
        cx = frame_w * 0.5
        cy = frame_h * 0.5
        sx = cx + self.focal_length_px * x / depth
        sy = cy - self.focal_length_px * y / depth
        if not (-frame_w <= sx <= frame_w * 2 and -frame_h <= sy <= frame_h * 2):
            return None
        return int(round(sx)), int(round(sy))

    def unproject_norm(
        self,
        nx: float,
        ny: float,
        *,
        frame_w: int,
        frame_h: int,
        depth_z: float | None = None,
    ) -> tuple[float, float, float]:
        """Map normalized landmark coords (0..1) to world point on a depth plane."""
        z = self.desk_plane_z if depth_z is None else depth_z
        depth = self.camera_z - z
        cx = frame_w * 0.5
        cy = frame_h * 0.5
        sx = nx * frame_w
        sy = ny * frame_h
        x = (sx - cx) * depth / self.focal_length_px
        y = -(sy - cy) * depth / self.focal_length_px
        return (x, y, z)

    def projected_radius(
        self,
        center: tuple[float, float, float],
        radius: float,
        *,
        frame_w: int,
        frame_h: int,
    ) -> int:
        edge = (
            center[0] + radius,
            center[1],
            center[2],
        )
        p0 = self.project(center, frame_w=frame_w, frame_h=frame_h)
        p1 = self.project(edge, frame_w=frame_w, frame_h=frame_h)
        if p0 is None or p1 is None:
            return 4
        return max(4, int(round(math.hypot(p1[0] - p0[0], p1[1] - p0[1]))))

    def grid_points(
        self,
        *,
        size: float,
        divisions: int,
        z: float,
    ) -> list[tuple[float, float, float]]:
        half = size * 0.5
        step = size / max(divisions, 1)
        pts: list[tuple[float, float, float]] = []
        n = divisions + 1
        for i in range(n):
            t = -half + i * step
            pts.append((t, -half, z))
            pts.append((t, half, z))
            pts.append((-half, t, z))
            pts.append((half, t, z))
        return pts
