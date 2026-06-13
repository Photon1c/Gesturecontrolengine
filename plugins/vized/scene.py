"""3D scene graph for the Vized geometric editor."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from typing import Any


PRIMITIVE_KINDS = ("box", "sphere", "line", "plane", "point")


@dataclass
class Shape3D:
    id: str
    kind: str
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    color: tuple[int, int, int] = (0, 210, 255)
    opacity: float = 0.85
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "position": list(self.position),
            "rotation": list(self.rotation),
            "scale": list(self.scale),
            "color": list(self.color),
            "opacity": self.opacity,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Shape3D:
        return cls(
            id=str(raw.get("id", uuid.uuid4().hex[:8])),
            kind=str(raw.get("kind", "box")),
            position=tuple(float(v) for v in raw.get("position", [0, 0, 0])[:3]),
            rotation=tuple(float(v) for v in raw.get("rotation", [0, 0, 0])[:3]),
            scale=tuple(float(v) for v in raw.get("scale", [1, 1, 1])[:3]),
            color=tuple(int(v) for v in raw.get("color", [0, 210, 255])[:3]),
            opacity=float(raw.get("opacity", 0.85)),
            label=str(raw.get("label", "")),
        )


@dataclass
class Scene3D:
    shapes: list[Shape3D] = field(default_factory=list)
    selected_id: str | None = None

    def add(self, shape: Shape3D) -> None:
        self.shapes.append(shape)
        self.selected_id = shape.id

    def remove_selected(self) -> bool:
        if not self.selected_id:
            return False
        before = len(self.shapes)
        self.shapes = [s for s in self.shapes if s.id != self.selected_id]
        if len(self.shapes) < before:
            self.selected_id = self.shapes[-1].id if self.shapes else None
            return True
        return False

    def selected(self) -> Shape3D | None:
        if not self.selected_id:
            return None
        for shape in self.shapes:
            if shape.id == self.selected_id:
                return shape
        return None

    def cycle_selection(self, direction: int = 1) -> None:
        if not self.shapes:
            self.selected_id = None
            return
        if self.selected_id is None:
            self.selected_id = self.shapes[0].id
            return
        ids = [s.id for s in self.shapes]
        try:
            idx = ids.index(self.selected_id)
        except ValueError:
            self.selected_id = ids[0]
            return
        self.selected_id = ids[(idx + direction) % len(ids)]

    def new_shape(
        self,
        kind: str,
        position: tuple[float, float, float],
        *,
        color: tuple[int, int, int],
        scale: tuple[float, float, float] | None = None,
    ) -> Shape3D:
        defaults = {
            "box": (0.35, 0.35, 0.35),
            "sphere": (0.28, 0.28, 0.28),
            "line": (0.5, 0.02, 0.02),
            "plane": (1.2, 1.2, 0.02),
            "point": (0.08, 0.08, 0.08),
        }
        shape = Shape3D(
            id=uuid.uuid4().hex[:8],
            kind=kind if kind in PRIMITIVE_KINDS else "box",
            position=position,
            scale=scale or defaults.get(kind, (0.35, 0.35, 0.35)),
            color=color,
            label=kind,
        )
        self.add(shape)
        return shape

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "selected_id": self.selected_id,
            "shapes": [s.to_dict() for s in self.shapes],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Scene3D:
        scene = cls(
            shapes=[Shape3D.from_dict(item) for item in raw.get("shapes") or []],
            selected_id=raw.get("selected_id"),
        )
        return scene


def rotate_point(
    point: tuple[float, float, float],
    rotation_deg: tuple[float, float, float],
) -> tuple[float, float, float]:
    x, y, z = point
    rx, ry, rz = (math.radians(v) for v in rotation_deg)
    cy, sy = math.cos(ry), math.sin(ry)
    x1 = x * cy + z * sy
    z1 = -x * sy + z * cy
    cx, sx = math.cos(rx), math.sin(rx)
    y2 = y * cx - z1 * sx
    z2 = y * sx + z1 * cx
    cz, sz = math.cos(rz), math.sin(rz)
    x3 = x1 * cz - y2 * sz
    y3 = x1 * sz + y2 * cz
    return (x3, y3, z2)


def shape_corners(shape: Shape3D) -> list[tuple[float, float, float]]:
    sx, sy, sz = shape.scale
    hx, hy, hz = sx * 0.5, sy * 0.5, sz * 0.5
    local = [
        (-hx, -hy, -hz),
        (hx, -hy, -hz),
        (hx, hy, -hz),
        (-hx, hy, -hz),
        (-hx, -hy, hz),
        (hx, -hy, hz),
        (hx, hy, hz),
        (-hx, hy, hz),
    ]
    px, py, pz = shape.position
    out: list[tuple[float, float, float]] = []
    for lx, ly, lz in local:
        rx, ry, rz = rotate_point((lx, ly, lz), shape.rotation)
        out.append((px + rx, py + ry, pz + rz))
    return out


def shape_wireframe_edges(shape: Shape3D) -> list[tuple[int, int]]:
    if shape.kind == "line":
        return [(0, 1)]
    if shape.kind == "point":
        return []
    if shape.kind == "sphere":
        return []
    # box / plane
    return [
        (0, 1),
        (1, 2),
        (2, 3),
        (3, 0),
        (4, 5),
        (5, 6),
        (6, 7),
        (7, 4),
        (0, 4),
        (1, 5),
        (2, 6),
        (3, 7),
    ]


def line_endpoints(shape: Shape3D) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    sx, _, _ = shape.scale
    half = sx * 0.5
    a = rotate_point((-half, 0.0, 0.0), shape.rotation)
    b = rotate_point((half, 0.0, 0.0), shape.rotation)
    px, py, pz = shape.position
    return (
        (px + a[0], py + a[1], pz + a[2]),
        (px + b[0], py + b[1], pz + b[2]),
    )
