/** 24h colony schedule — mirrors plugins/crow/colony_cycle.py boundaries. */

export const SCHEDULE_SEGMENTS = [
  { phase: "sleep", start: 0, end: 4, label: "Sleep" },
  { phase: "awakening", start: 4, end: 5, label: "Awakening" },
  { phase: "morning_calls", start: 5, end: 6, label: "Morning calls" },
  { phase: "dispersal", start: 6, end: 8, label: "Dispersal" },
  { phase: "foraging", start: 8, end: 12, label: "Foraging" },
  { phase: "patrol", start: 12, end: 17, label: "Patrol" },
  { phase: "gathering_calls", start: 17, end: 18, label: "Gathering calls" },
  { phase: "roost_convergence", start: 18, end: 20, label: "Roost convergence" },
  { phase: "social_chatter", start: 20, end: 21, label: "Social chatter" },
  { phase: "settling", start: 21, end: 23, label: "Settling" },
  { phase: "sleep", start: 23, end: 24, label: "Sleep" },
];

/** Minimum strip-body height so all phase rows + metrics fit without scrolling. */
export const STRIP_LAYOUT = {
  phaseRowPx: 30,
  phaseListPadPx: 8,
  subtitlePx: 58,
  metricsPx: 92,
  sectionGapPx: 14,
};

export function roostStripMinHeight() {
  const { phaseRowPx, phaseListPadPx, subtitlePx, metricsPx, sectionGapPx } = STRIP_LAYOUT;
  return (
    SCHEDULE_SEGMENTS.length * phaseRowPx +
    phaseListPadPx +
    subtitlePx +
    metricsPx +
    sectionGapPx
  );
}

export function segmentWidth(start, end) {
  return ((end - start) / 24) * 100;
}

export function formatHour(hour) {
  const h = Math.floor(hour) % 24;
  const m = Math.round((hour % 1) * 60);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

export function normalizeCallDensity(callsPerMin) {
  return Math.min(1, Math.max(0, (callsPerMin || 0) / 40));
}

const SHORT_LABELS = {
  sleep: "Slp",
  awakening: "Awk",
  morning_calls: "Call",
  dispersal: "Out",
  foraging: "Frg",
  patrol: "Pat",
  gathering_calls: "Gath",
  roost_convergence: "Home",
  social_chatter: "Soc",
  settling: "Set",
};

/** Full label when segment is wide enough, otherwise short form. */
export function phaseLabel(seg, widthPct) {
  if (widthPct >= 6.5) return seg.label;
  if (widthPct >= 3.5) return SHORT_LABELS[seg.phase] || seg.label.slice(0, 4);
  return SHORT_LABELS[seg.phase]?.slice(0, 3) || seg.label.slice(0, 3);
}
