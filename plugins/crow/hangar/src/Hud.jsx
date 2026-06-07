const showDebugPanel = new URLSearchParams(location.search).has("debug");

function pct(value, scale = 1) {
  return `${Math.round((value || 0) * scale * 100)}%`;
}

export default function Hud({
  state,
  error,
  cameraLabel,
  autoMode = true,
  controlMode = "auto",
  setAutoMode,
  autoPending = false,
  autoError = null,
}) {
  const meta = state?.meta || {};
  const hud = meta.hud || {};
  const c = state?.controls || {};
  const lead = state?.lead || {};
  const flight = state?.status || {};
  const pos = lead.position || [0, 0, 0];
  const vel = lead.velocity || [0, 0, 0];
  const scale = hud.metric_scale ?? 1;

  const colony = state?.colony;
  const inputRegistered = meta.input_registered === true;
  const confidence = meta.tracking_confidence ?? 0;
  const leadAgent = colony?.agents?.find((a) => a.id === "lead");
  const wingAgents = (colony?.agents || []).filter((a) => a.id !== "lead");
  const phase = error || meta.phase || (state ? "ready" : "connecting…");
  const agentLine = leadAgent
    ? `Pilot ${leadAgent.state}${wingAgents.length ? ` · wings ${wingAgents.map((a) => a.state).join(", ")}` : ""}`
    : null;

  const energyLow = hud.energy_low_threshold ?? 0.32;
  const warn = hud.stall_warn_threshold ?? 0.42;
  const critical = hud.stall_critical_threshold ?? 0.68;

  const statusParts = [];
  let flightClass = "stat";

  if (hud.show_energy !== false) {
    const energy = (flight.energy || 0) * scale;
    statusParts.push(`${energy <= energyLow ? "LOW energy" : "energy"} ${pct(flight.energy, scale)}`);
    if (energy <= energyLow) flightClass += " low-energy";
  }
  if (hud.show_lift !== false) statusParts.push(`lift ${pct(flight.lift, scale)}`);
  if (hud.show_drag) statusParts.push(`drag ${pct(flight.drag, scale)}`);
  if (hud.show_stall_risk !== false) {
    const stall = (flight.stall_risk || 0) * scale;
    if (stall >= critical) {
      statusParts.push(`!!! STALL ${pct(flight.stall_risk, scale)}`);
      flightClass += " critical";
    } else if (stall >= warn) {
      statusParts.push(`STALL ${pct(flight.stall_risk, scale)}`);
      flightClass += " warn";
    } else if (stall >= warn * 0.55) {
      statusParts.push(`sink ${pct(flight.stall_risk, scale)}`);
    }
  }

  const controlsLine = autoMode
    ? `Routine patrol · glide ${c.glide ? "on" : "off"} · bank ${(c.bank || 0).toFixed(2)}`
    : inputRegistered
      ? `INPUT OK · flap ${pct(c.flap_power, scale)} · span ${(c.wingspan || 0).toFixed(2)} · bank ${(c.bank || 0).toFixed(2)}`
      : meta.tracking
        ? `POSE WEAK (${pct(confidence)}) — move into frame, show arms`
        : "MANUAL — step into webcam frame to pilot";

  const telemetryLine = state
    ? `alt ${pos[1].toFixed(1)} · spd ${Math.hypot(vel[0], vel[1], vel[2]).toFixed(1)} · cam ${cameraLabel} · flock ${(state.flock || []).length}`
    : "—";

  const controlClass = autoMode ? "control-badge control-auto" : "control-badge control-manual";
  const inputClass = autoMode
    ? controlClass
    : inputRegistered
      ? "input-badge input-ok"
      : meta.tracking
        ? "input-badge input-weak"
        : "input-badge input-lost";

  return (
    <>
      <div id="hud">
        <h1>Blackwing — Crow Hangar</h1>
        <div className="control-row">
          <div className={inputClass}>
            <span className="input-dot" />
            {autoMode
              ? `AUTO · ${colony?.daily_cycle || "daily cycle"}`
              : inputRegistered
                ? `MANUAL · input (${pct(confidence)})`
                : meta.tracking
                  ? `MANUAL · weak pose (${pct(confidence)})`
                  : "MANUAL · awaiting pose"}
          </div>
          {setAutoMode ? (
            <div className="mode-toggle" role="group" aria-label="Flight control mode">
              <button
                type="button"
                className={`mode-toggle-btn ${autoMode ? "active" : ""}`}
                disabled={autoPending}
                onClick={() => setAutoMode(true)}
              >
                Auto
              </button>
              <button
                type="button"
                className={`mode-toggle-btn ${!autoMode ? "active" : ""}`}
                disabled={autoPending}
                onClick={() => setAutoMode(false)}
              >
                Manual
              </button>
            </div>
          ) : null}
        </div>
        {autoError ? <div className="stat toggle-error">{autoError}</div> : null}
        <div className="mode">{phase}</div>
        {agentLine ? <div className="stat">{agentLine}</div> : null}
        {colony?.phase_note && autoMode ? (
          <div className="stat cycle-note">{colony.phase_note}</div>
        ) : null}
        <div className="stat">{controlsLine}</div>
        <div className={flightClass}>{statusParts.join(" · ") || "—"}</div>
        <div className="stat">{telemetryLine}</div>
        {colony?.greeting ? <div className="stat roost-greeting">{colony.greeting}</div> : null}
        {colony?.roost?.name ? (
          <div className="stat">
            {colony.roost.name} · {colony.daily_cycle || "—"} · pop {colony.roost.population}
          </div>
        ) : null}
        <div className="stat camera-hint">1-4 cameras · M map · OpenCV: A toggles mode</div>
      </div>
      {showDebugPanel && state?.debug ? (
        <div id="debug-panel" style={{ display: "block" }}>
          {`debug\nLy ${state.debug.raw_left_wrist_y}  Ry ${state.debug.raw_right_wrist_y}\n`}
          {`Lvy ${state.debug.left_wrist_vy}  Rvy ${state.debug.right_wrist_vy}\n`}
          {`conf ${state.debug.tracking_confidence}`}
        </div>
      ) : null}
    </>
  );
}
