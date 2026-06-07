"""Crow flight dynamics: thrust, lift, bank, glide, and perch."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .flight_controller import FlightControls


@dataclass
class CrowState:
    """Single crow in world space (Three.js Y-up convention)."""

    id: str
    x: float = 0.0
    y: float = 10.0
    z: float = 0.0
    vx: float = 0.0
    vy: float = 0.0
    vz: float = 0.0
    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0
    wing_phase: float = 0.0
    mode: str = "hover"


@dataclass
class FlockState:
    crows: list[CrowState] = field(default_factory=list)


TELEMETRY_SCHEMA_VERSION = 1


class CrowPhysics:
    def __init__(self, cfg: dict[str, Any], colony: Any = None) -> None:
        self.cfg = cfg
        self._colony = colony
        self._energy = float(cfg.get("initial_energy", 0.78))
        self._stall_risk_ema = 0.0
        self._prev_yaw = float(cfg.get("spawn_heading", 0.0))
        self.flock = FlockState(crows=[])

    def set_flock(self, crows: list[CrowState]) -> None:
        self.flock.crows = list(crows)
        if crows:
            self._prev_yaw = crows[0].yaw

    def add_crow(self, crow: CrowState) -> None:
        self.flock.crows.append(crow)

    def reset_runtime_state(self) -> None:
        """Restore energy/stall after flock reset — avoids drained startup flight."""
        self._energy = float(self.cfg.get("initial_energy", 0.78))
        self._stall_risk_ema = 0.0

    def _effective_flap(self, controls: FlightControls) -> float:
        energy_floor = float(self.cfg.get("energy_power_floor", 0.58))
        energy_band = energy_floor + (1.0 - energy_floor) * self._energy
        reward_exp = float(self.cfg.get("flap_reward_exponent", 0.84))
        boost = float(self.cfg.get("flap_reward_boost", 1.22))
        shaped = min(1.0, (controls.flap_power**reward_exp) * boost)
        return min(1.0, shaped * energy_band)

    def step(self, controls: FlightControls, dt: float, colony: Any = None) -> CrowState:
        colony = colony or self._colony
        lead = self.flock.crows[0]
        if not controls.tracking:
            lead.mode = "lost"
            self._apply_drag(lead, dt, drag_scale=2.5)
            self._integrate(lead, dt)
            self._follow_flock(lead, controls, dt)
            return lead

        effective_flap = self._effective_flap(controls)
        lift_scale = float(self.cfg.get("lift_scale", 7.2))
        thrust_scale = float(self.cfg.get("thrust_scale", 5.2))
        turn_scale = float(self.cfg.get("turn_scale", 2.6))
        dive_scale = float(self.cfg.get("dive_scale", 3.0))
        glide_lift = float(self.cfg.get("glide_lift", 0.48))
        gravity = float(self.cfg.get("gravity", 2.0))
        perch_drag = float(self.cfg.get("perch_drag", 4.0))

        if controls.perch:
            lead.mode = "perch"
            lead.vx *= max(0.0, 1.0 - perch_drag * dt)
            lead.vy *= max(0.0, 1.0 - perch_drag * dt)
            lead.vz *= max(0.0, 1.0 - perch_drag * dt)
            lead.roll *= max(0.0, 1.0 - 2.0 * dt)
        elif controls.glide:
            lead.mode = "glide"
            g_factor = float(self.cfg.get("glide_gravity_factor", 0.18))
            if controls.autonomous and colony is not None:
                # Autopilot cruise: hold routine altitude instead of net-positive glide lift.
                target_y = colony.routine_target(lead)[1]
                alt_hold = float(self.cfg.get("auto_altitude_hold", 2.4))
                lead.vy += (target_y - lead.y) * alt_hold * dt
                lead.vy -= gravity * float(self.cfg.get("auto_glide_gravity", 0.62)) * dt
                vy_cap = float(self.cfg.get("auto_vertical_speed_cap", 2.5))
                lead.vy = max(-vy_cap, min(vy_cap, lead.vy))
            else:
                lead.vy += glide_lift * dt
                lead.vy -= gravity * g_factor * dt
            cruise = float(self.cfg.get("glide_cruise_speed", 3.8))
            stability = float(self.cfg.get("glide_speed_stability", 1.4))
            self._seek_speed(lead, cruise, stability * dt)
            bank_abs = abs(controls.bank_steering)
            lateral_damp = float(self.cfg.get("glide_lateral_damp", 0.55)) * max(
                0.0, 1.0 - bank_abs * float(self.cfg.get("bank_damp_relief", 2.8))
            )
            self._damp_lateral(lead, dt, lateral_damp)
            lead.roll *= max(0.0, 1.0 - float(self.cfg.get("glide_roll_damp", 2.2)) * dt)
            lead.pitch *= max(0.0, 1.0 - float(self.cfg.get("glide_pitch_damp", 2.0)) * dt)
            if effective_flap > 0.05:
                self._thrust_forward(lead, effective_flap * thrust_scale * 0.12 * dt)
        else:
            lead.mode = "flap"
            lift_burst = float(self.cfg.get("flap_lift_burst", 1.35))
            thrust_burst = float(self.cfg.get("flap_thrust_burst", 1.2))
            lead.vy += effective_flap * lift_scale * lift_burst * dt
            thrust = effective_flap * thrust_scale * thrust_burst * dt
            self._thrust_forward(lead, thrust)
            lead.vy -= gravity * dt

        pitch_thrust = controls.pitch * dive_scale * dt
        self._thrust_forward(lead, pitch_thrust)

        if controls.autonomous and not controls.perch:
            # Autopilot: direct heading hold — avoid bank/lateral fighting that causes spins.
            auto_yaw = float(self.cfg.get("auto_yaw_rate", 1.35))
            lead.yaw += controls.bank_steering * auto_yaw * dt
            cruise = float(self.cfg.get("auto_cruise_speed", 3.6))
            hold = float(self.cfg.get("auto_heading_hold", 3.8)) * dt
            self._seek_speed(lead, cruise, hold)
            self._damp_lateral(
                lead, dt, float(self.cfg.get("auto_lateral_damp", 5.5))
            )
        else:
            bank_scale = turn_scale * (
                float(self.cfg.get("glide_bank_scale", 0.45)) if controls.glide else 1.0
            )
            speed_boost = 1.0 + min(
                float(self.cfg.get("turn_speed_boost_cap", 0.65)),
                math.hypot(lead.vx, lead.vz) * float(self.cfg.get("turn_speed_boost", 0.12)),
            )
            yaw_rate = (
                controls.bank_steering
                * bank_scale
                * float(self.cfg.get("yaw_rate_scale", 2.1))
                * speed_boost
            )
            lead.yaw += yaw_rate * dt
            self._apply_bank_lateral(lead, controls, dt)

        if not controls.perch:
            self._maintain_min_forward(lead, dt)
        if colony is not None:
            gx, gz = colony.guidance_force(lead, dt)
            lead.vx += gx * float(self.cfg.get("roost_guidance", 8.0))
            lead.vz += gz * float(self.cfg.get("roost_guidance", 8.0))

        wing_rate = float(self.cfg.get("wing_phase_rate", 9.5))
        lead.wing_phase = (lead.wing_phase + effective_flap * wing_rate * dt) % (
            2 * math.pi
        )

        drag_scale = float(self.cfg.get("glide_drag_scale", 0.55)) if controls.glide else 1.0
        self._apply_drag(lead, dt, drag_scale=drag_scale)
        self._integrate(lead, dt)
        self._clamp_speed(lead)
        self._clamp_altitude(lead)
        self._soft_bounds(lead, colony)
        self._update_attitude(lead, controls, dt)
        self._update_energy(controls, effective_flap, lead, dt)
        self._follow_flock(lead, controls, dt)
        return lead

    def _update_attitude(self, lead: CrowState, controls: FlightControls, dt: float) -> None:
        """Coordinated flight: heading from velocity, pitch from climb, bank into turns."""
        h_speed = math.hypot(lead.vx, lead.vz)

        steer_dead = float(self.cfg.get("steer_deadzone", 0.025))
        if (
            not controls.autonomous
            and h_speed > float(self.cfg.get("heading_sync_min_speed", 0.35))
            and abs(controls.bank_steering) < steer_dead
        ):
            target_yaw = math.atan2(lead.vx, lead.vz)
            yaw_blend = min(1.0, float(self.cfg.get("yaw_sync_strength", 2.8)) * dt)
            delta = math.atan2(
                math.sin(target_yaw - lead.yaw),
                math.cos(target_yaw - lead.yaw),
            )
            lead.yaw += delta * yaw_blend

        if controls.perch:
            target_pitch = 0.0
            target_roll = 0.0
        elif h_speed > 0.4:
            climb_pitch = math.atan2(-lead.vy, h_speed) * float(
                self.cfg.get("climb_pitch_scale", 0.55)
            )
            dive_pitch = controls.pitch * float(self.cfg.get("dive_pitch_scale", 0.32))
            glide_pitch = 0.08 if controls.glide else 0.0
            target_pitch = climb_pitch + dive_pitch + glide_pitch
            target_pitch = max(-0.55, min(0.55, target_pitch))
            if controls.autonomous:
                target_roll = controls.bank_steering * float(
                    self.cfg.get("auto_roll_scale", 0.32)
                )
            else:
                bank_scale = float(self.cfg.get("bank_angle_scale", 0.92))
                target_roll = controls.bank_steering * bank_scale
                yaw_rate = (lead.yaw - self._prev_yaw) / max(dt, 1e-3)
                target_roll += yaw_rate * float(
                    self.cfg.get("coordinated_roll_scale", 0.45)
                )
            target_roll = max(-0.75, min(0.75, target_roll))
        else:
            target_pitch = controls.pitch * 0.15
            target_roll = controls.bank_steering * 0.35

        attitude_lag = min(1.0, float(self.cfg.get("attitude_lag", 9.0)) * dt)
        lead.pitch += (target_pitch - lead.pitch) * attitude_lag
        lead.roll += (target_roll - lead.roll) * attitude_lag
        self._prev_yaw = lead.yaw

    def _thrust_forward(self, lead: CrowState, amount: float) -> None:
        lead.vx += math.sin(lead.yaw) * amount
        lead.vz += math.cos(lead.yaw) * amount

    def _apply_bank_lateral(
        self, lead: CrowState, controls: FlightControls, dt: float
    ) -> None:
        bank = controls.bank_steering
        if abs(bank) < 0.015:
            return
        h_speed = math.hypot(lead.vx, lead.vz)
        speed_factor = max(
            float(self.cfg.get("bank_lateral_min_factor", 0.35)),
            min(1.0, h_speed / float(self.cfg.get("bank_lateral_speed_ref", 5.0))),
        )
        lateral = (
            bank
            * float(self.cfg.get("bank_lateral_scale", 2.4))
            * speed_factor
            * dt
        )
        lead.vx += math.cos(lead.yaw) * lateral
        lead.vz -= math.sin(lead.yaw) * lateral

    def _damp_lateral(self, lead: CrowState, dt: float, damp: float) -> None:
        if damp <= 0.0:
            return
        forward_v = lead.vx * math.sin(lead.yaw) + lead.vz * math.cos(lead.yaw)
        lateral_v = lead.vx * math.cos(lead.yaw) - lead.vz * math.sin(lead.yaw)
        lateral_v *= max(0.0, 1.0 - damp * dt)
        lead.vx = math.sin(lead.yaw) * forward_v + math.cos(lead.yaw) * lateral_v
        lead.vz = math.cos(lead.yaw) * forward_v - math.sin(lead.yaw) * lateral_v

    def _seek_speed(self, lead: CrowState, speed: float, alpha: float) -> None:
        desired_vx = math.sin(lead.yaw) * speed
        desired_vz = math.cos(lead.yaw) * speed
        lead.vx += (desired_vx - lead.vx) * alpha
        lead.vz += (desired_vz - lead.vz) * alpha

    def _maintain_min_forward(self, lead: CrowState, dt: float) -> None:
        min_speed = float(self.cfg.get("min_forward_speed", 1.4))
        h = math.hypot(lead.vx, lead.vz)
        if h < min_speed:
            boost = (min_speed - h) * float(self.cfg.get("min_forward_gain", 2.2)) * dt
            self._thrust_forward(lead, boost)

    def _update_energy(
        self,
        controls: FlightControls,
        effective_flap: float,
        lead: CrowState,
        dt: float,
    ) -> None:
        recharge = effective_flap * float(self.cfg.get("energy_recharge_rate", 0.42))
        if effective_flap > 0.45:
            recharge += float(self.cfg.get("energy_flap_bonus", 0.18)) * dt
        spend = math.hypot(lead.vx, lead.vy, lead.vz) * float(
            self.cfg.get("energy_spend_rate", 0.028)
        )
        idle_drain = float(self.cfg.get("energy_idle_drain", 0.008))
        if controls.glide:
            idle_drain *= float(self.cfg.get("glide_energy_drain_scale", 0.35))
        self._energy += (recharge - spend) * dt - idle_drain * dt
        self._energy = max(0.0, min(1.0, self._energy))

    def _apply_drag(self, crow: CrowState, dt: float, drag_scale: float = 1.0) -> None:
        drag = float(self.cfg.get("drag", 0.38)) * drag_scale
        factor = max(0.0, 1.0 - drag * dt)
        crow.vx *= factor
        crow.vy *= factor
        crow.vz *= factor

    def _integrate(self, crow: CrowState, dt: float) -> None:
        crow.x += crow.vx * dt
        crow.y += crow.vy * dt
        crow.z += crow.vz * dt

    def _clamp_speed(self, crow: CrowState) -> None:
        max_speed = float(self.cfg.get("max_speed", 16.0))
        speed = math.hypot(crow.vx, crow.vy, crow.vz)
        if speed > max_speed > 0:
            scale = max_speed / speed
            crow.vx *= scale
            crow.vy *= scale
            crow.vz *= scale

    def _soft_bounds(self, crow: CrowState, colony: Any = None) -> None:
        if colony is not None:
            radius = colony.roost.territory_radius * 1.15
            dx = crow.x - colony.roost.x
            dz = crow.z - colony.roost.z
            dist = math.hypot(dx, dz)
            if dist > radius and dist > 1e-6:
                crow.x = colony.roost.x + (dx / dist) * radius
                crow.z = colony.roost.z + (dz / dist) * radius
                crow.vx *= 0.65
                crow.vz *= 0.65
            return
        x_limit = float(self.cfg.get("world_x_limit", 80.0))
        z_limit = float(self.cfg.get("world_z_limit", 120.0))
        if abs(crow.x) > x_limit:
            crow.x = math.copysign(x_limit, crow.x)
            crow.vx *= 0.5
        if crow.z < -z_limit:
            crow.z = -z_limit
            crow.vz *= 0.5
        elif crow.z > float(self.cfg.get("world_z_max", 20.0)):
            crow.z = float(self.cfg.get("world_z_max", 20.0))
            crow.vz *= 0.5

    def _clamp_altitude(self, crow: CrowState) -> None:
        floor_y = float(self.cfg.get("floor_altitude", 1.5))
        ceiling_y = float(self.cfg.get("ceiling_altitude", 80.0))
        if crow.y < floor_y:
            crow.y = floor_y
            crow.vy = max(0.0, crow.vy)
        if crow.y > ceiling_y:
            crow.y = ceiling_y
            crow.vy = min(0.0, crow.vy)

    def _follow_flock(
        self, lead: CrowState, controls: FlightControls, dt: float
    ) -> None:
        behind = float(self.cfg.get("formation_spacing", 2.8))
        lateral = float(self.cfg.get("formation_lateral", 1.4))
        colony = self._colony
        _lateral_scale = {
            "scout": 1.35,
            "sentinel": 0.72,
            "juvenile": 1.18,
            "wing": 0.92,
            "forager": 1.05,
        }
        _lag_scale = {"wing": 1.18, "sentinel": 1.12, "juvenile": 0.82, "scout": 0.94}

        for i, follower in enumerate(self.flock.crows[1:], start=1):
            agent = colony.get_agent(follower.id) if colony else None
            role = agent.role if agent else "wing"

            lag = (0.35 + i * 0.12) * _lag_scale.get(role, 1.0)
            side = (
                lateral
                * _lateral_scale.get(role, 1.0)
                * (1 if i % 2 else -1)
                * ((i + 1) // 2)
            )
            target_x = lead.x - math.sin(lead.yaw) * behind * i + math.cos(lead.yaw) * side
            target_z = lead.z - math.cos(lead.yaw) * behind * i - math.sin(lead.yaw) * side
            target_y = lead.y - 0.8 - i * 0.35

            if role == "sentinel" and colony is not None:
                dx = colony.roost.x - follower.x
                dz = colony.roost.z - follower.z
                dist = math.hypot(dx, dz)
                if dist > 1e-6:
                    pull = min(2.5, dist * 0.04) * dt
                    target_x += (dx / dist) * pull
                    target_z += (dz / dist) * pull

            follower.x += (target_x - follower.x) * lag * dt
            follower.y += (target_y - follower.y) * lag * dt
            follower.z += (target_z - follower.z) * lag * dt

            follower.vx = (target_x - follower.x) * lag
            follower.vy = (target_y - follower.y) * lag
            follower.vz = (target_z - follower.z) * lag

            if role == "scout":
                wobble = 0.18 * dt
                follower.vx += math.sin(follower.wing_phase * 1.3 + i) * wobble
                follower.vz += math.cos(follower.wing_phase * 1.1 + i) * wobble * 0.85

            yaw = lead.yaw
            if role == "juvenile":
                yaw += math.sin(lead.wing_phase + i * 0.7) * 0.14
            follower.yaw = yaw
            follower.pitch = lead.pitch * (0.82 if role == "juvenile" else 0.88)
            follower.roll = lead.roll * (0.65 if role == "juvenile" else 0.75)
            follower.wing_phase = lead.wing_phase + i * 0.4
            follower.mode = lead.mode

    def flight_status(self, lead: CrowState, controls: FlightControls) -> dict[str, float]:
        """Stable HUD metrics — normalized 0..1 unless noted."""
        speed = math.hypot(lead.vx, lead.vy, lead.vz)
        speed_norm = min(
            1.0, speed / max(float(self.cfg.get("status_speed_normalize", 12.0)), 1e-6)
        )
        effective_flap = self._effective_flap(controls)

        if controls.perch:
            lift = 0.05
        elif controls.glide:
            lift = min(
                1.0,
                float(self.cfg.get("glide_lift", 0.48))
                / max(float(self.cfg.get("lift_scale", 7.2)), 1e-6)
                + effective_flap * 0.25
                + 0.35,
            )
        else:
            lift = min(1.0, effective_flap * 1.05)

        drag = min(1.0, speed_norm * float(self.cfg.get("drag", 0.38)) * 2.0)

        descending = 1.0 if lead.vy < -1.0 else max(0.0, -lead.vy / 3.0)
        low_thrust = 1.0 - min(1.0, abs(lead.vz) / 3.5)
        low_flap = 1.0 - effective_flap
        glide_penalty = float(self.cfg.get("glide_stall_penalty", 0.25)) if controls.glide else 1.0
        raw_stall = min(
            1.0,
            (descending * 0.5 + low_thrust * 0.28 + low_flap * 0.22) * glide_penalty,
        )
        stall_alpha = float(self.cfg.get("stall_smoothing", 0.28))
        self._stall_risk_ema = stall_alpha * raw_stall + (1.0 - stall_alpha) * self._stall_risk_ema

        return {
            "energy": round(self._energy, 3),
            "lift": round(lift, 3),
            "drag": round(drag, 3),
            "stall_risk": round(self._stall_risk_ema, 3),
        }

    def to_telemetry(
        self,
        lead: CrowState,
        controls: FlightControls,
        colony: Any = None,
    ) -> dict[str, Any]:
        colony = colony or self._colony
        agent_by_id = {}
        if colony is not None:
            colony_data = colony.to_telemetry(self.flock.crows)
            agent_by_id = {a["id"]: a for a in colony_data.get("agents", [])}
        """Stable /api/state contract — see README_BLACKWING.md."""
        return {
            "schema_version": TELEMETRY_SCHEMA_VERSION,
            "lead": {
                "position": [round(lead.x, 3), round(lead.y, 3), round(lead.z, 3)],
                "velocity": [round(lead.vx, 3), round(lead.vy, 3), round(lead.vz, 3)],
                "rotation": [round(lead.pitch, 3), round(lead.yaw, 3), round(lead.roll, 3)],
            },
            "controls": {
                "flap_power": controls.flap_power,
                "wingspan": controls.wingspan,
                "bank": controls.bank_steering,
                "pitch": controls.pitch,
                "glide": controls.glide,
                "perch": controls.perch,
                "autonomous": controls.autonomous,
            },
            "status": self.flight_status(lead, controls),
            "flock": [
                {
                    "id": c.id,
                    "position": [round(c.x, 3), round(c.y, 3), round(c.z, 3)],
                    "rotation": [round(c.pitch, 3), round(c.yaw, 3), round(c.roll, 3)],
                    "wing_phase": round(c.wing_phase, 3),
                    "mode": c.mode,
                    "home_roost": agent_by_id.get(c.id, {}).get("home_roost"),
                    "energy": agent_by_id.get(c.id, {}).get("energy"),
                    "curiosity": agent_by_id.get(c.id, {}).get("curiosity"),
                    "state": agent_by_id.get(c.id, {}).get("state"),
                    "agent_state": agent_by_id.get(c.id, {}).get("state"),
                    "role": agent_by_id.get(c.id, {}).get("role"),
                    "sex": agent_by_id.get(c.id, {}).get("sex"),
                    "spawn_order": agent_by_id.get(c.id, {}).get("spawn_order"),
                }
                for c in self.flock.crows
            ],
        }
