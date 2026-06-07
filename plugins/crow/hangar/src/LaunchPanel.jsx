import { useState } from "react";

const FORMATIONS = [
  { id: "patrol_wedge", label: "Patrol wedge" },
  { id: "roost_cluster", label: "Roost cluster" },
  { id: "scout_line", label: "Scout line" },
];

const PRESETS = [
  { id: "balanced", label: "Balanced" },
  { id: "patrol_heavy", label: "Patrol heavy" },
  { id: "scout_heavy", label: "Scout heavy" },
  { id: "juvenile_heavy", label: "Juvenile heavy" },
];

export default function LaunchPanel({ launch, count, maxCrows, onReset, onSpawn }) {
  const [open, setOpen] = useState(false);
  const [startCount, setStartCount] = useState(count || 3);
  const [formation, setFormation] = useState(launch?.formation || "patrol_wedge");
  const [preset, setPreset] = useState(launch?.preset || "balanced");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function run(action) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : "launch failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div id="launch-panel">
      <button type="button" className="launch-toggle" onClick={() => setOpen((v) => !v)}>
        {open ? "▾ Launch" : "▸ Launch"}
      </button>
      {open ? (
        <div className="launch-body">
          <label className="launch-row">
            Start count
            <input
              type="range"
              min={1}
              max={maxCrows}
              value={startCount}
              onChange={(e) => setStartCount(Number(e.target.value))}
            />
            <span>{startCount}</span>
          </label>
          <label className="launch-row">
            Formation
            <select value={formation} onChange={(e) => setFormation(e.target.value)}>
              {FORMATIONS.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.label}
                </option>
              ))}
            </select>
          </label>
          <label className="launch-row">
            Role mix
            <select value={preset} onChange={(e) => setPreset(e.target.value)}>
              {PRESETS.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
          <div className="launch-actions">
            <button
              type="button"
              disabled={busy}
              onClick={() => run(() => onReset({ count: startCount, formation, preset }))}
            >
              Reset flock
            </button>
            <button
              type="button"
              disabled={busy || count >= maxCrows}
              onClick={() => run(() => onSpawn({}))}
            >
              + Spawn crow
            </button>
          </div>
          <div className="launch-stat">
            Flock {count}/{maxCrows} · {formation} · {preset}
          </div>
          {error ? <div className="launch-error">{error}</div> : null}
        </div>
      ) : null}
    </div>
  );
}
