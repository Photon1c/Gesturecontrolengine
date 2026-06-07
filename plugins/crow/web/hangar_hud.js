/** Telemetry HUD — plain script, no CDN deps. Runs even if Three.js fails. */
(function () {
  const phaseEl = document.getElementById("phase");
  const controlsEl = document.getElementById("controls");
  const flightStatusEl = document.getElementById("flight-status");
  const telemetryEl = document.getElementById("telemetry");
  const debugPanel = document.getElementById("debug-panel");
  const showDebugPanel = new URLSearchParams(location.search).has("debug");

  function pct(value, scale) {
    return `${Math.round((value || 0) * (scale || 1) * 100)}%`;
  }

  async function pollState() {
    try {
      const res = await fetch("/api/state", { cache: "no-store" });
      if (!res.ok) {
        phaseEl.textContent = `api error ${res.status}`;
        return;
      }
      updateHud(await res.json());
    } catch {
      phaseEl.textContent = "waiting for pilot…";
    }
  }

  function updateHud(state) {
    const meta = state.meta || {};
    const hud = meta.hud || {};
    const c = state.controls || {};
    const lead = state.lead || {};
    const flight = state.status || {};
    const pos = lead.position || [0, 0, 0];
    const vel = lead.velocity || [0, 0, 0];
    const scale = hud.metric_scale ?? 1;

    phaseEl.textContent = meta.phase || state.schema_version ? "ready" : "—";

    if (meta.tracking) {
      controlsEl.textContent = `flap ${pct(c.flap_power, scale)} · span ${(c.wingspan || 0).toFixed(2)} · bank ${(c.bank || 0).toFixed(2)}`;
    } else {
      controlsEl.textContent = "step into camera frame";
    }

    const statusParts = [];
    const energyLow = hud.energy_low_threshold ?? 0.32;
    const warn = hud.stall_warn_threshold ?? 0.42;
    const critical = hud.stall_critical_threshold ?? 0.68;
    flightStatusEl.className = "stat";

    if (hud.show_energy !== false) {
      const energy = (flight.energy || 0) * scale;
      statusParts.push(`${energy <= energyLow ? "LOW energy" : "energy"} ${pct(flight.energy, scale)}`);
      if (energy <= energyLow) flightStatusEl.classList.add("low-energy");
    }
    if (hud.show_lift !== false) statusParts.push(`lift ${pct(flight.lift, scale)}`);
    if (hud.show_drag) statusParts.push(`drag ${pct(flight.drag, scale)}`);
    if (hud.show_stall_risk !== false) {
      const stall = (flight.stall_risk || 0) * scale;
      if (stall >= critical) {
        statusParts.push(`!!! STALL ${pct(flight.stall_risk, scale)}`);
        flightStatusEl.classList.add("critical");
      } else if (stall >= warn) {
        statusParts.push(`STALL ${pct(flight.stall_risk, scale)}`);
        flightStatusEl.classList.add("warn");
      } else if (stall >= warn * 0.55) {
        statusParts.push(`sink ${pct(flight.stall_risk, scale)}`);
      }
    }
    flightStatusEl.textContent = statusParts.join(" · ") || "—";
    telemetryEl.textContent = `alt ${pos[1].toFixed(1)} · spd ${Math.abs(vel[2]).toFixed(1)} · flock ${(state.flock || []).length}`;

    if (showDebugPanel && state.debug) {
      debugPanel.style.display = "block";
      const d = state.debug;
      debugPanel.textContent =
        `debug\nLy ${d.raw_left_wrist_y}  Ry ${d.raw_right_wrist_y}\n` +
        `Lvy ${d.left_wrist_vy}  Rvy ${d.right_wrist_vy}\nconf ${d.tracking_confidence}`;
    } else {
      debugPanel.style.display = "none";
    }

    window.__blackwingState = state;
  }

  setInterval(pollState, 33);
  pollState();
})();
