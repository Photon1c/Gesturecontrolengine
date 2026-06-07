"""Routine autopilot — stable daily-cycle flight when player control is off."""

from __future__ import annotations

import math
from typing import Any

from .flight_controller import FlightControls
from .physics import CrowState
from .roost import Colony


class RoutineAutoPilot:
    """Drive the lead crow along colony waypoints with smooth glide-dominated flight."""

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        self._turn_smooth = 0.0
        self._pitch_smooth = 0.0

    def generate(self, lead: CrowState, colony: Colony) -> FlightControls:
        phase = colony.cycle_phase
        target = colony.routine_target(lead)

        if phase == "night_roost":
            self._turn_smooth *= 0.85
            self._pitch_smooth *= 0.85
            return FlightControls(
                flap_power=0.0,
                wingspan=0.12,
                bank_steering=0.0,
                pitch=0.0,
                glide=False,
                perch=True,
                tracking=True,
                autonomous=True,
            )

        dx = target[0] - lead.x
        dz = target[2] - lead.z
        dy = target[1] - lead.y
        dist_h = math.hypot(dx, dz)

        desired_yaw = math.atan2(dx, dz) if dist_h > 1.0 else lead.yaw
        yaw_err = math.atan2(
            math.sin(desired_yaw - lead.yaw),
            math.cos(desired_yaw - lead.yaw),
        )

        deadzone = float(self.cfg.get("yaw_deadzone", 0.12))
        if abs(yaw_err) < deadzone:
            yaw_err = 0.0

        # Scale turn demand down near waypoint to prevent overshoot spins.
        if dist_h < float(self.cfg.get("approach_slow_distance", 14.0)):
            yaw_err *= max(0.25, dist_h / float(self.cfg.get("approach_slow_distance", 14.0)))

        turn_gain = float(self.cfg.get("turn_gain", 0.85))
        turn_raw = max(-1.0, min(1.0, yaw_err * turn_gain))
        turn_alpha = float(self.cfg.get("turn_smoothing", 0.28))
        self._turn_smooth = turn_alpha * turn_raw + (1.0 - turn_alpha) * self._turn_smooth

        pitch_gain = float(self.cfg.get("altitude_pitch_gain", 0.12))
        pitch_raw = max(-0.35, min(0.35, -dy * pitch_gain))
        pitch_alpha = float(self.cfg.get("pitch_smoothing", 0.28))
        self._pitch_smooth = pitch_alpha * pitch_raw + (1.0 - pitch_alpha) * self._pitch_smooth

        flap = 0.0
        if dy > float(self.cfg.get("climb_flap_threshold", 1.8)):
            flap = float(self.cfg.get("climb_flap_power", 0.42))
        elif dy > float(self.cfg.get("hold_flap_threshold", 0.55)):
            flap = float(self.cfg.get("hold_flap_power", 0.18))
        elif phase == "morning_departure" and dist_h > float(
            self.cfg.get("departure_flap_distance", 14.0)
        ):
            flap = float(self.cfg.get("departure_flap_power", 0.22))

        glide = flap < float(self.cfg.get("glide_flap_cutoff", 0.3))
        wingspan = float(self.cfg.get("glide_wingspan", 0.58)) if glide else 0.4

        # bank_steering carries normalized turn intent for autonomous physics.
        return FlightControls(
            flap_power=round(flap, 4),
            wingspan=round(wingspan, 4),
            bank_steering=round(self._turn_smooth, 4),
            pitch=round(self._pitch_smooth, 4),
            glide=glide,
            perch=False,
            tracking=True,
            autonomous=True,
        )
