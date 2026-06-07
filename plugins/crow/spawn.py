"""Ordered flock spawn — formations, role presets, deterministic positions."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .physics import CrowState

FORMATIONS = ("roost_cluster", "patrol_wedge", "scout_line")
PRESETS = ("balanced", "patrol_heavy", "scout_heavy", "juvenile_heavy")
ROLES = ("lead", "scout", "sentinel", "forager", "juvenile", "wing")

_PRESET_ROLES: dict[str, list[str]] = {
    "balanced": ["scout", "forager", "sentinel", "juvenile", "wing"],
    "patrol_heavy": ["wing", "sentinel", "wing", "forager", "sentinel"],
    "scout_heavy": ["scout", "scout", "forager", "scout", "wing"],
    "juvenile_heavy": ["juvenile", "juvenile", "wing", "forager", "juvenile"],
}

_ROLE_CURIOSITY: dict[str, float] = {
    "lead": 0.55,
    "scout": 0.72,
    "sentinel": 0.48,
    "forager": 0.58,
    "juvenile": 0.78,
    "wing": 0.52,
}


@dataclass
class CrowSpawnSpec:
    id: str
    role: str
    sex: str
    spawn_order: int
    x: float
    y: float
    z: float
    energy: float = 0.78
    curiosity: float = 0.55
    state: str = "PATROLLING"


def crow_id_for_order(order: int) -> str:
    if order == 1:
        return "lead"
    if order == 2:
        return "wing_1"
    if order == 3:
        return "wing_2"
    return f"crow_{order:03d}"


def _jitter(index: int, axis: int) -> float:
    return 0.18 * math.sin(index * 1.73 + axis * 0.91)


def formation_position(
    formation: str,
    index: int,
    total: int,
    roost_x: float,
    roost_y: float,
    roost_z: float,
    spawn_yaw: float,
    *,
    spawn_altitude: float | None = None,
) -> tuple[float, float, float]:
    """Deterministic spawn offsets with light jitter."""
    base_alt = spawn_altitude if spawn_altitude is not None else roost_y + 3.5
    alt = base_alt + (index % 3) * 0.35
    if formation == "scout_line":
        lateral = (index - (total - 1) / 2.0) * 3.2
        forward = 7.0
        x = roost_x + lateral + _jitter(index, 0)
        z = roost_z + forward + _jitter(index, 1)
        return x, alt, z

    if formation == "patrol_wedge":
        if index == 0:
            return roost_x + _jitter(0, 0), alt, roost_z + 7.5 + _jitter(0, 1)
        side = -1.0 if index % 2 else 1.0
        row = (index + 1) // 2
        x = roost_x + side * (2.0 + row * 1.4) + _jitter(index, 0)
        z = roost_z + 5.5 - row * 1.2 + _jitter(index, 1)
        return x, alt - row * 0.2, z

    # roost_cluster (default)
    angle = (index / max(total, 1)) * math.pi * 2
    radius = 2.0 + (index % 2) * 0.6
    x = roost_x + math.sin(angle) * radius + _jitter(index, 0)
    z = roost_z + math.cos(angle) * radius + 5.5 + _jitter(index, 1)
    return x, alt, z


def roles_for_count(count: int, preset: str) -> list[str]:
    """First bird lead, next two wing, remainder from preset cycle."""
    if count <= 0:
        return []
    preset = preset if preset in _PRESET_ROLES else "balanced"
    pool = _PRESET_ROLES[preset]
    roles: list[str] = []
    for order in range(1, count + 1):
        if order == 1:
            roles.append("lead")
        elif order <= 3:
            roles.append("wing")
        else:
            roles.append(pool[(order - 4) % len(pool)])
    return roles


class FlockSpawner:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        self._formation = str(cfg.get("default_formation", "patrol_wedge"))
        self._preset = str(cfg.get("default_preset", "balanced"))
        self._next_order = 1

    @property
    def formation(self) -> str:
        return self._formation

    @property
    def preset(self) -> str:
        return self._preset

    def max_crows(self) -> int:
        return int(self.cfg.get("max_crows", 12))

    def default_count(self) -> int:
        return int(self.cfg.get("default_count", 3))

    def build_specs(
        self,
        count: int,
        *,
        formation: str | None = None,
        preset: str | None = None,
        colony: Any,
    ) -> list[CrowSpawnSpec]:
        formation = formation if formation in FORMATIONS else self._formation
        preset = preset if preset in PRESETS else self._preset
        self._formation = formation
        self._preset = preset

        count = max(1, min(count, self.max_crows()))
        roles = roles_for_count(count, preset)
        spawn_yaw = float(self.cfg.get("spawn_heading", 0.0))
        spawn_alt = float(self.cfg.get("spawn_altitude", colony.roost.y + 6.0))

        specs: list[CrowSpawnSpec] = []
        for i, role in enumerate(roles):
            order = i + 1
            x, y, z = formation_position(
                formation,
                i,
                count,
                colony.roost.x,
                colony.roost.y,
                colony.roost.z,
                spawn_yaw,
                spawn_altitude=spawn_alt,
            )
            specs.append(
                CrowSpawnSpec(
                    id=crow_id_for_order(order),
                    role=role,
                    sex="unknown",
                    spawn_order=order,
                    x=x,
                    y=y,
                    z=z,
                    energy=0.78,
                    curiosity=_ROLE_CURIOSITY.get(role, 0.55),
                    state="PATROLLING",
                )
            )
        self._next_order = count + 1
        return specs

    def append_spec(
        self,
        current_count: int,
        *,
        role: str | None = None,
        sex: str = "unknown",
        colony: Any,
    ) -> CrowSpawnSpec | None:
        if current_count >= self.max_crows():
            return None
        order = current_count + 1
        if order == 1:
            role = "lead"
        elif order <= 3 and role is None:
            role = "wing"
        elif role is None or role not in ROLES:
            pool = _PRESET_ROLES.get(self._preset, _PRESET_ROLES["balanced"])
            role = pool[(order - 4) % len(pool)] if order > 3 else "wing"

        x, y, z = formation_position(
            self._formation,
            current_count,
            order,
            colony.roost.x,
            colony.roost.y,
            colony.roost.z,
            float(self.cfg.get("spawn_heading", 0.0)),
            spawn_altitude=float(self.cfg.get("spawn_altitude", colony.roost.y + 6.0)),
        )
        self._next_order = order + 1
        return CrowSpawnSpec(
            id=crow_id_for_order(order),
            role=role,
            sex=sex if sex in ("unknown", "male", "female") else "unknown",
            spawn_order=order,
            x=x,
            y=y,
            z=z,
            energy=0.78,
            curiosity=_ROLE_CURIOSITY.get(role, 0.55),
            state="PATROLLING",
        )

    def spec_to_crow(self, spec: CrowSpawnSpec) -> CrowState:
        spawn_yaw = float(self.cfg.get("spawn_heading", 0.0))
        forward = float(self.cfg.get("spawn_forward_speed", 3.2))
        return CrowState(
            id=spec.id,
            x=spec.x,
            y=spec.y,
            z=spec.z,
            vx=math.sin(spawn_yaw) * forward,
            vz=math.cos(spawn_yaw) * forward,
            yaw=spawn_yaw,
        )
