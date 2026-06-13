"""Blackwing Pilot — camera-driven flock control orchestrator."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .auto_pilot import RoutineAutoPilot
from .flight_controller import FlightController, FlightControls
from .physics import CrowPhysics, CrowState, TELEMETRY_SCHEMA_VERSION
from .roost import Colony
from .spawn import CrowSpawnSpec, FlockSpawner
from .telemetry_server import TelemetryServer


class BlackwingPilot:
    """Corvid Flight Lab: pose → controls → physics → Three.js telemetry."""

    name = "blackwing"

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        self._enabled = bool(cfg.get("enabled", True))
        self._mode = str(cfg.get("mode", "static")).strip().lower()
        if self._mode not in ("static", "dynamic"):
            self._mode = "static"
        self._market_bridge = None
        if self._mode == "dynamic":
            try:
                from plugins.market.bridge import MarketBridge

                self._market_bridge = MarketBridge(cfg.get("market", {}))
            except ImportError:
                self._market_bridge = None
        self._hud_cfg = cfg.get("hud", {})
        self._debug_cfg = cfg.get("debug", {})
        auto_cfg = cfg.get("auto_mode", {})
        self._auto_mode = bool(auto_cfg.get("enabled_by_default", True))
        self._flight = FlightController(cfg.get("controls", {}))
        self._auto = RoutineAutoPilot(auto_cfg)
        roost_cfg = dict(cfg.get("roost", {}))
        if "daily_cycle" not in roost_cfg:
            roost_cfg["daily_cycle"] = cfg.get("daily_cycle", {})
        if cfg.get("environment"):
            roost_cfg["environment"] = cfg.get("environment")
        self._colony = Colony(roost_cfg)
        self._physics = CrowPhysics(cfg.get("physics", {}), self._colony)
        spawn_cfg = dict(cfg.get("spawn", {}))
        spawn_cfg.setdefault("spawn_heading", cfg.get("physics", {}).get("spawn_heading", 0.0))
        spawn_cfg.setdefault(
            "spawn_forward_speed", cfg.get("physics", {}).get("spawn_forward_speed", 3.2)
        )
        spawn_cfg.setdefault(
            "spawn_altitude",
            roost_cfg.get("daily_cycle", {}).get(
                "patrol_altitude", cfg.get("physics", {}).get("spawn_altitude", 14.0)
            ),
        )
        self._spawner = FlockSpawner(spawn_cfg)
        self.reset_flock(
            count=self._spawner.default_count(),
            formation=self._spawner.formation,
            preset=self._spawner.preset,
        )
        telemetry_cfg = cfg.get("telemetry", {})
        web_root = Path(__file__).resolve().parent / "web"
        self._telemetry = TelemetryServer(
            host=str(telemetry_cfg.get("host", "127.0.0.1")),
            port=int(telemetry_cfg.get("port", 8765)),
            web_root=web_root,
            on_control=self._on_remote_control,
        )
        self._telemetry_started = False
        self._last_hud = "Auto patrol — press A to take manual control"
        self._last_telemetry: dict[str, Any] = {}
        sim_cfg = cfg.get("simulation", {})
        self._simulation_speed = float(sim_cfg.get("default_speed", 1.0))
        self._sim_clock = self._initial_sim_clock(sim_cfg, roost_cfg)

    def _initial_sim_clock(self, sim_cfg: dict[str, Any], roost_cfg: dict[str, Any]) -> float:
        """Simulated seconds offset — optional start_hour jumps into a schedule phase."""
        cycle_cfg = roost_cfg.get("daily_cycle", {})
        compressed = float(cycle_cfg.get("compressed_cycle_minutes", 20) or 20)
        day_seconds = compressed * 60.0
        start_hour = sim_cfg.get("start_hour")
        if start_hour is not None:
            return (float(start_hour) / 24.0) * day_seconds
        return 0.0

    def set_simulation_speed(self, speed: float) -> float:
        self._simulation_speed = max(0.25, min(8.0, float(speed)))
        return self._simulation_speed

    def _on_remote_control(self, payload: dict[str, Any]) -> None:
        action = payload.get("action")
        if action == "reset_flock":
            self.reset_flock(
                count=int(payload.get("count", self._spawner.default_count())),
                formation=payload.get("formation"),
                preset=payload.get("preset"),
            )
            return
        if action == "spawn_crow":
            self.spawn_crow(
                role=payload.get("role"),
                sex=str(payload.get("sex", "unknown")),
            )
            return
        if action == "set_simulation_speed":
            self.set_simulation_speed(float(payload.get("speed", 1.0)))
            return
        if "simulation_speed" in payload:
            self.set_simulation_speed(float(payload["simulation_speed"]))
            return
        if "auto_mode" in payload:
            self.set_auto_mode(bool(payload["auto_mode"]))
        elif payload.get("toggle_auto"):
            self.toggle_auto_mode()

    def _apply_spawn_specs(self, specs: list[CrowSpawnSpec]) -> None:
        self._colony.clear_agents()
        crows: list[CrowState] = []
        for spec in specs:
            self._colony.register_spawn(
                crow_id=spec.id,
                role=spec.role,
                sex=spec.sex,
                spawn_order=spec.spawn_order,
                energy=spec.energy,
                curiosity=spec.curiosity,
                state=spec.state,
            )
            crows.append(self._spawner.spec_to_crow(spec))
        self._physics.set_flock(crows)

    def reset_flock(
        self,
        *,
        count: int = 3,
        formation: str | None = None,
        preset: str | None = None,
    ) -> int:
        specs = self._spawner.build_specs(
            count,
            formation=formation,
            preset=preset,
            colony=self._colony,
        )
        self._apply_spawn_specs(specs)
        self._physics.reset_runtime_state()
        self._auto._turn_smooth = 0.0
        self._auto._pitch_smooth = 0.0
        return len(specs)

    def spawn_crow(self, *, role: str | None = None, sex: str = "unknown") -> bool:
        spec = self._spawner.append_spec(
            len(self._physics.flock.crows),
            role=role,
            sex=sex,
            colony=self._colony,
        )
        if spec is None:
            return False
        self._colony.register_spawn(
            crow_id=spec.id,
            role=spec.role,
            sex=spec.sex,
            spawn_order=spec.spawn_order,
            energy=spec.energy,
            curiosity=spec.curiosity,
            state=spec.state,
        )
        self._physics.add_crow(self._spawner.spec_to_crow(spec))
        return True

    def _launch_meta(self) -> dict[str, Any]:
        return {
            "formation": self._spawner.formation,
            "preset": self._spawner.preset,
            "count": len(self._physics.flock.crows),
            "max_crows": self._spawner.max_crows(),
            "simulation_speed": round(self._simulation_speed, 2),
        }

    def set_auto_mode(self, enabled: bool) -> bool:
        self._auto_mode = bool(enabled)
        return self._auto_mode

    def toggle_auto_mode(self) -> bool:
        self._auto_mode = not self._auto_mode
        return self._auto_mode

    @property
    def auto_mode(self) -> bool:
        return self._auto_mode

    def ensure_telemetry(self) -> str:
        if not self._telemetry_started:
            self._telemetry.start()
            self._telemetry_started = True
            self._telemetry.publish(self._boot_payload())
        return self._telemetry.url

    def hangar_mode(self) -> str:
        dist_index = Path(__file__).resolve().parent / "web" / "dist" / "index.html"
        return "vite" if dist_index.is_file() else "legacy"

    def _idle_controls(self) -> FlightControls:
        return FlightControls(
            flap_power=0.0,
            wingspan=0.58,
            bank_steering=0.0,
            pitch=0.0,
            glide=True,
            perch=False,
            tracking=True,
            autonomous=self._auto_mode,
        )

    def _snapshot_telemetry(
        self,
        *,
        phase: str,
        tracking: bool,
        tracking_confidence: float,
        input_registered: bool,
    ) -> dict[str, Any]:
        if not self._physics.flock.crows:
            self.reset_flock()
        lead = self._physics.flock.crows[0]
        controls = self._idle_controls()
        telemetry = self._physics.to_telemetry(lead, controls, self._colony)
        telemetry["colony"] = self._colony.to_telemetry(self._physics.flock.crows)
        telemetry["meta"] = self._meta(
            phase=phase,
            tracking=tracking,
            tracking_confidence=tracking_confidence,
            input_registered=input_registered,
        )
        if self._market_bridge is not None:
            telemetry["market"] = self._market_bridge.telemetry_slice()
        elif self._mode == "dynamic":
            telemetry["market"] = {"mode": "dynamic", "available": False, "error": "market bridge unavailable"}
        return telemetry

    def _boot_payload(self) -> dict[str, Any]:
        return self._snapshot_telemetry(
            phase="auto · boot",
            tracking=False,
            tracking_confidence=0.0,
            input_registered=False,
        )

    def _meta(
        self,
        *,
        phase: str,
        tracking: bool,
        tracking_confidence: float,
        input_registered: bool,
    ) -> dict[str, Any]:
        return {
            "phase": phase,
            "tracking": tracking,
            "tracking_confidence": round(tracking_confidence, 4),
            "input_registered": input_registered,
            "auto_mode": self._auto_mode,
            "control_mode": "auto" if self._auto_mode else "manual",
            "hangar": self._telemetry.url if self._telemetry_started else None,
            "mode": self._physics.flock.crows[0].mode if self._physics.flock.crows else "hover",
            "hud": {
                **self._hud_display_cfg(),
                "metric_scale": float(self._hud_cfg.get("metric_scale", 1.0)),
            },
            "launch": self._launch_meta(),
            "simulation_speed": round(self._simulation_speed, 2),
            "blackwing_mode": self._mode,
        }

    def _hud_display_cfg(self) -> dict[str, Any]:
        return {
            "show_energy": bool(self._hud_cfg.get("show_energy", True)),
            "show_lift": bool(self._hud_cfg.get("show_lift", True)),
            "show_drag": bool(self._hud_cfg.get("show_drag", False)),
            "show_stall_risk": bool(self._hud_cfg.get("show_stall_risk", True)),
            "show_controls_summary": bool(
                self._hud_cfg.get("show_controls_summary", True)
            ),
            "stall_warn_threshold": float(
                self._hud_cfg.get("stall_warn_threshold", 0.42)
            ),
            "stall_critical_threshold": float(
                self._hud_cfg.get("stall_critical_threshold", 0.68)
            ),
            "energy_low_threshold": float(
                self._hud_cfg.get("energy_low_threshold", 0.32)
            ),
        }

    def update(self, ts: float, pose_result: Any, dt: float) -> dict[str, Any]:
        if not self._physics.flock.crows:
            self.reset_flock()
        pose_controls = self._flight.update(ts, pose_result)
        lead_snapshot = self._physics.flock.crows[0]

        sim_dt = dt * self._simulation_speed
        self._sim_clock += sim_dt

        if self._auto_mode:
            controls = self._auto.generate(lead_snapshot, self._colony, sim_dt)
            player_control = False
        else:
            controls = pose_controls
            player_control = True

        lead = self._physics.step(controls, sim_dt, self._colony)
        self._colony.update(
            self._physics.flock.crows,
            player_control=player_control,
            player_tracking=pose_controls.tracking,
            player_perch=pose_controls.perch if player_control else False,
            wall_ts=self._sim_clock,
            sim_dt=sim_dt,
        )
        telemetry = self._physics.to_telemetry(lead, controls, self._colony)
        telemetry["colony"] = self._colony.to_telemetry(self._physics.flock.crows)

        snap = self._flight.debug_snapshot()
        confidence = float(snap.get("tracking_confidence", 0.0))
        input_threshold = float(self._hud_cfg.get("input_confidence_threshold", 0.55))
        input_registered = (
            player_control
            and pose_controls.tracking
            and confidence >= input_threshold
        )

        cycle = telemetry["colony"].get("daily_cycle", "—")
        if self._auto_mode:
            phase = f"auto · {cycle}"
        elif not pose_controls.tracking:
            phase = "manual · awaiting pose"
        elif pose_controls.perch:
            phase = "manual · perch"
        elif pose_controls.glide:
            phase = "manual · glide"
        else:
            phase = "manual · flying"

        telemetry["meta"] = self._meta(
            phase=phase,
            tracking=controls.tracking,
            tracking_confidence=confidence if player_control else 0.0,
            input_registered=input_registered,
        )
        if self._market_bridge is not None:
            telemetry["market"] = self._market_bridge.telemetry_slice()
        elif self._mode == "dynamic":
            telemetry["market"] = {"mode": "dynamic", "available": False, "error": "market bridge unavailable"}
        if self._debug_cfg.get("enabled", False):
            telemetry["debug"] = self._flight.debug_snapshot()
        self._telemetry.publish(telemetry)
        self._last_telemetry = telemetry
        self._last_hud = self._format_hud(
            controls,
            pose_controls,
            lead,
            telemetry["status"],
            telemetry.get("colony"),
            player_control=player_control,
            confidence=confidence,
        )
        return telemetry

    def hud_line(self) -> str:
        return self._last_hud

    def debug_line(self) -> str | None:
        if not self._debug_cfg.get("enabled", False):
            return None
        d = self._flight.debug_snapshot()
        return (
            f"dbg Ly {d['raw_left_wrist_y']:.2f} Ry {d['raw_right_wrist_y']:.2f} "
            f"Lvy {d['left_wrist_vy']:+.2f} Rvy {d['right_wrist_vy']:+.2f} "
            f"conf {d['tracking_confidence']:.0%}"
        )

    def _format_hud(
        self,
        controls: FlightControls,
        pose_controls: FlightControls,
        lead: CrowState,
        status: dict[str, float],
        colony: dict[str, Any] | None = None,
        *,
        player_control: bool,
        confidence: float,
    ) -> str:
        cycle = (colony or {}).get("daily_cycle", "—")
        note = (colony or {}).get("phase_note", "")

        if not player_control:
            lead_agent = "—"
            for a in (colony or {}).get("agents") or []:
                if a.get("id") == "lead":
                    lead_agent = a.get("state", "—")
                    break
            parts = [f"AUTO · {cycle} · {lead_agent}"]
            if note:
                parts.append(note.split("—")[0].strip()[:28])
            parts.append(f"alt {lead.y:.1f}")
            parts.append("A = manual")
            return " | ".join(parts)

        if not pose_controls.tracking:
            return f"MANUAL · {cycle} | step into frame | A = auto"

        lead_agent = "—"
        for a in (colony or {}).get("agents") or []:
            if a.get("id") == "lead":
                lead_agent = a.get("state", "—")
                break

        parts: list[str] = [f"MANUAL · {lead.mode} · {lead_agent}"]
        if self._hud_cfg.get("show_controls_summary", True):
            if controls.perch:
                parts.append("perch")
            elif controls.glide:
                parts.append("glide — arms wide")
            elif controls.flap_power > 0.12:
                parts.append(f"flap {controls.flap_power:.0%} — pump down")
            elif controls.pitch > 0.2:
                parts.append("dive — reach forward")
            elif controls.pitch < -0.2:
                parts.append("climb — pull back")
            elif abs(controls.bank_steering) > 0.04:
                parts.append(
                    "turn right — left wrist up"
                    if controls.bank_steering > 0
                    else "turn left — right wrist up"
                )
            else:
                parts.append("neutral — flap/turn/glide")

        parts.append(f"alt {lead.y:.1f}")
        parts.append(f"conf {confidence:.0%}")
        parts.append("A = auto")

        scale = float(self._hud_cfg.get("metric_scale", 1.0))
        energy = status["energy"] * scale
        if self._hud_cfg.get("show_energy", True):
            low = float(self._hud_cfg.get("energy_low_threshold", 0.32))
            tag = "LOW E" if energy <= low else "E"
            parts.append(f"{tag} {energy:.0%}")

        if self._hud_cfg.get("show_lift", True):
            parts.append(f"lift {status['lift'] * scale:.0%}")

        if self._hud_cfg.get("show_stall_risk", True):
            stall = status["stall_risk"] * scale
            warn = float(self._hud_cfg.get("stall_warn_threshold", 0.42))
            critical = float(self._hud_cfg.get("stall_critical_threshold", 0.68))
            if stall >= critical:
                parts.append(f"!!! STALL {stall:.0%}")
            elif stall >= warn:
                parts.append(f"STALL {stall:.0%}")

        return " | ".join(parts)

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "name": "Blackwing Pilot",
            "schema_version": TELEMETRY_SCHEMA_VERSION,
            "hangar": self._telemetry.url if self._telemetry_started else None,
            "flock_size": len(self._physics.flock.crows),
            "auto_mode": self._auto_mode,
            "last_telemetry": self._last_telemetry.get("status"),
            "debug_enabled": bool(self._debug_cfg.get("enabled", False)),
        }

    @property
    def enabled(self) -> bool:
        return self._enabled

    def close(self) -> None:
        self._telemetry.stop()

    @staticmethod
    def system_prompt() -> str:
        return (
            "Blackwing Pilot — colony starts in AUTO mode (daily cycle patrol).\n"
            "Press A to toggle MANUAL control.\n"
            "Manual gestures: pump both arms down = flap/lift | arms wide = glide\n"
            "Raise LEFT wrist = turn RIGHT | raise RIGHT wrist = turn LEFT\n"
            "Reach forward = dive | pull back = climb | arms in + still = perch\n"
            "Open the Crow Hangar in Chrome/Edge for the Three.js flock view."
        )
