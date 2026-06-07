import { useEffect, useRef, useState } from "react";
import {
  SCHEDULE_SEGMENTS,
  formatHour,
  normalizeCallDensity,
  roostStripMinHeight,
  segmentWidth,
} from "./scheduleStrip.js";

function MetricRow({ label, value, display, tone = "coherence" }) {
  const pct = Math.round(Math.min(1, Math.max(0, value)) * 100);
  return (
    <div className="strip-metric-row">
      <span className="strip-metric-label">{label}</span>
      <div className="strip-metric-track">
        <div className={`strip-metric-fill strip-metric-${tone}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="strip-metric-value">{display}</span>
    </div>
  );
}

function formatHourRange(start, end) {
  return `${String(Math.floor(start)).padStart(2, "0")}:00–${String(Math.floor(end)).padStart(2, "0")}:00`;
}

export default function RoostStrip({
  colony,
  simulationSpeed,
  visible,
  width,
  height,
  onResize,
  onToggle,
}) {
  const panelRef = useRef(null);
  const [resizing, setResizing] = useState(false);

  useEffect(() => {
    if (!resizing) return undefined;
    function onMove(e) {
      const panel = panelRef.current;
      if (!panel) return;
      const rect = panel.getBoundingClientRect();
      const nextW = rect.right - e.clientX;
      const nextH = rect.bottom - e.clientY;
      onResize(nextW, nextH);
    }
    function onUp() {
      setResizing(false);
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [resizing, onResize]);

  if (!visible) {
    return (
      <button type="button" id="strip-toggle-hidden" onClick={onToggle}>
        Roost day (R)
      </button>
    );
  }

  const hour = colony?.cycle_hour ?? 0;
  const phase = colony?.daily_cycle || "—";
  const nextPhase = colony?.next_phase;
  const roostName = colony?.roost?.name || "Roost";
  const coherence = colony?.coherence ?? 0;
  const pressure = colony?.pressure ?? 0;
  const deviation = colony?.phase_deviation ?? 0;
  const calls = colony?.call_density ?? 0;
  const observed = colony?.observed_behavior;
  const bodyHeight = Math.max(height, roostStripMinHeight());

  return (
    <div
      id="roost-strip"
      ref={panelRef}
      style={{ width: width + 16, minHeight: bodyHeight + 36 }}
    >
      <div className="strip-header">
        <span className="strip-title">Roost day · 24h</span>
        <button type="button" className="strip-hide" onClick={onToggle} title="Hide (R)">
          ×
        </button>
      </div>

      <div className="strip-body" style={{ minHeight: bodyHeight }}>
        <div className="strip-subtitle">
          <div className="strip-subtitle-line">
            <strong>{roostName}</strong> · {formatHour(hour)}
          </div>
          <div className="strip-subtitle-line">
            {phase.replace(/_/g, " ")}
            {nextPhase ? ` → ${nextPhase.replace(/_/g, " ")}` : ""}
            {simulationSpeed && simulationSpeed !== 1 ? ` · ${simulationSpeed}×` : ""}
          </div>
          {observed ? (
            <div className="strip-subtitle-line strip-observed">Observed: {observed.replace(/_/g, " ")}</div>
          ) : null}
        </div>

        <div className="strip-phase-list">
          {SCHEDULE_SEGMENTS.map((seg) => {
            const span = segmentWidth(seg.start, seg.end);
            const active = seg.phase === phase && hour >= seg.start && hour < seg.end;
            return (
              <div
                key={`${seg.phase}-${seg.start}`}
                className={`strip-phase-row ${active ? "active" : ""}`}
                title={`${seg.label} (${formatHourRange(seg.start, seg.end)})`}
              >
                <span className="strip-phase-now">{active ? "▸" : ""}</span>
                <div className="strip-phase-copy">
                  <span className="strip-phase-name">{seg.label}</span>
                  <span className="strip-phase-hours">{formatHourRange(seg.start, seg.end)}</span>
                </div>
                <div className="strip-phase-bar-track">
                  <div className="strip-phase-bar-fill" style={{ width: `${span}%` }} />
                </div>
              </div>
            );
          })}
        </div>

        <div className="strip-metrics">
          <MetricRow
            label="Coherence"
            value={coherence}
            display={coherence.toFixed(2)}
            tone="coherence"
          />
          <MetricRow label="Pressure" value={pressure} display={pressure.toFixed(2)} tone="pressure" />
          <MetricRow
            label="Calls"
            value={normalizeCallDensity(calls)}
            display={`${Math.round(calls)}/min`}
            tone="calls"
          />
          <MetricRow
            label="Deviation"
            value={deviation}
            display={deviation.toFixed(2)}
            tone="deviation"
          />
        </div>
      </div>

      <div
        className="strip-resize"
        onMouseDown={() => setResizing(true)}
        title="Drag to resize"
      />
    </div>
  );
}
