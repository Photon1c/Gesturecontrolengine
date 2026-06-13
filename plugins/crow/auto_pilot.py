"""Routine autopilot — stable daily-cycle flight when player control is off."""

from __future__ import annotations

import math
from typing import Any

from .flight_controller import FlightControls
from .physics import CrowState
from .colony_cycle import SCHEDULE_OUTBOUND, SCHEDULE_RETURN, SCHEDULE_ROOST_PERCH
from .roost import Colony


class RoutineAutoPilot:
    """Drive the lead crow along colony waypoints with smooth glide-dominated flight."""

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        self._turn_smooth = 0.0
        self._pitch_smooth = 0.0

    def generate(
        self, lead: CrowState, colony: Colony, sim_dt: float = 0.033
    ) -> FlightControls:
        schedule = colony.schedule_phase
        legacy = colony.cycle_phase
        lead_agent = colony.get_agent(lead.id)
        if lead_agent and lead_agent.state == "DRINKING":
            self._turn_smooth *= 0.85
            self._pitch_smooth *= 0.85
            return FlightControls(
                flap_power=0.0,
                wingspan=0.14,
                bank_steering=0.0,
                pitch=-0.05,
                glide=False,
                perch=True,
                tracking=True,
                autonomous=True,
            )
        if lead_agent and lead_agent.state == "WAITING":
            target = colony.environment.resource_target_for(lead, schedule)
            if target:
                dx = target[0] - lead.x
                dz = target[2] - lead.z
                dist_h = math.hypot(dx, dz)
                if dist_h > 1.0:
                    desired_yaw = math.atan2(dx, dz)
                    yaw_err = math.atan2(
                        math.sin(desired_yaw - lead.yaw),
                        math.cos(desired_yaw - lead.yaw),
                    )
                    self._turn_smooth = 0.2 * max(-0.4, min(0.4, yaw_err * 0.6)) + 0.8 * self._turn_smooth
            return FlightControls(
                flap_power=0.0,
                wingspan=0.52,
                bank_steering=round(self._turn_smooth, 4),
                pitch=0.0,
                glide=True,
                perch=False,
                tracking=True,
                autonomous=True,
            )

        target = colony.routine_target(lead, sim_dt)

        arrive_dist = float(self.cfg.get("perch_arrival_distance", 1.6))
        arrive_alt = float(self.cfg.get("perch_arrival_altitude", 1.1))

        if colony.escape_active():
            target = colony.environment.escape_target(lead)
            dx = target[0] - lead.x
            dz = target[2] - lead.z
            dy = target[1] - lead.y
            dist_h = math.hypot(dx, dz)
            desired_yaw = math.atan2(dx, dz) if dist_h > 1.0 else lead.yaw
            yaw_err = math.atan2(
                math.sin(desired_yaw - lead.yaw),
                math.cos(desired_yaw - lead.yaw),
            )
            turn_raw = max(-1.0, min(1.0, yaw_err * float(self.cfg.get("turn_gain", 0.85))))
            self._turn_smooth = 0.45 * turn_raw + 0.55 * self._turn_smooth
            pitch_raw = max(-0.2, min(0.45, -dy * float(self.cfg.get("altitude_pitch_gain", 0.12))))
            self._pitch_smooth = 0.4 * pitch_raw + 0.6 * self._pitch_smooth
            flap = float(self.cfg.get("escape_flap_power", 0.55))
            return FlightControls(
                flap_power=round(flap, 4),
                wingspan=0.42,
                bank_steering=round(self._turn_smooth, 4),
                pitch=round(self._pitch_smooth, 4),
                glide=False,
                perch=False,
                tracking=True,
                autonomous=True,
            )

        if schedule in SCHEDULE_ROOST_PERCH or legacy == "night_roost":
            perch_target = colony.roost_perch_target()
            dist_home = math.hypot(lead.x - perch_target[0], lead.z - perch_target[2])
            alt_err = abs(lead.y - perch_target[1])
            if dist_home > arrive_dist * 2.5 or alt_err > arrive_alt * 2.0:
                target = perch_target
            else:
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
        elif schedule in SCHEDULE_RETURN:
            perch_target = colony.roost_perch_target()
            dist_perch = math.hypot(lead.x - perch_target[0], lead.z - perch_target[2])
            cutover = float(self.cfg.get("return_perch_cutover", 13.0))
            if dist_perch < cutover:
                target = perch_target

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

        # Scale turn demand near waypoint — keep gentle alignment to prevent orbit drift.
        slow_dist = float(self.cfg.get("approach_slow_distance", 14.0))
        brake_dist = float(self.cfg.get("approach_brake_distance", 10.0))
        if dist_h < slow_dist:
            align_floor = float(self.cfg.get("approach_align_floor", 0.35))
            yaw_err *= max(align_floor, dist_h / slow_dist)
        if dist_h < brake_dist:
            self._turn_smooth *= 0.92

        turn_gain = float(self.cfg.get("turn_gain", 0.85))
        if dist_h > 18.0:
            turn_gain *= 0.72
        turn_raw = max(-1.0, min(1.0, yaw_err * turn_gain))
        turn_alpha = float(self.cfg.get("turn_smoothing", 0.28))
        self._turn_smooth = turn_alpha * turn_raw + (1.0 - turn_alpha) * self._turn_smooth

        pitch_gain = float(self.cfg.get("altitude_pitch_gain", 0.12))
        pitch_raw = max(-0.35, min(0.35, -dy * pitch_gain))
        # Ease into perch altitude on return — less dive-and-pull oscillation.
        if schedule in SCHEDULE_RETURN or schedule in SCHEDULE_ROOST_PERCH:
            pitch_raw *= min(1.0, dist_h / max(brake_dist, 1.0))
        pitch_alpha = float(self.cfg.get("pitch_smoothing", 0.28))
        self._pitch_smooth = pitch_alpha * pitch_raw + (1.0 - pitch_alpha) * self._pitch_smooth

        flap = 0.0
        if dy > float(self.cfg.get("climb_flap_threshold", 1.8)):
            flap = float(self.cfg.get("climb_flap_power", 0.42))
        elif dy > float(self.cfg.get("hold_flap_threshold", 0.55)):
            flap = float(self.cfg.get("hold_flap_power", 0.18))
        elif legacy == "morning_departure" and dist_h > float(
            self.cfg.get("departure_flap_distance", 14.0)
        ):
            flap = float(self.cfg.get("departure_flap_power", 0.22))
        elif schedule in SCHEDULE_OUTBOUND and dist_h > 10.0 and lead.y < float(
            self.cfg.get("takeoff_altitude", 12.0)
        ):
            flap = float(self.cfg.get("departure_flap_power", 0.28))
        elif dist_h < brake_dist and abs(dy) > 0.4:
            flap = float(self.cfg.get("approach_flap_power", 0.12))

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
