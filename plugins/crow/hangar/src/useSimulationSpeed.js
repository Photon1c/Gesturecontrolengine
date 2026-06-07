import { useCallback } from "react";

const SPEEDS = [0.5, 1, 2, 4];

async function postControl(payload) {
  const res = await fetch("/api/control", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    throw new Error(`speed POST failed (${res.status})`);
  }
  return res.json();
}

export function useSimulationSpeed(state) {
  const speed =
    state?.meta?.simulation_speed ??
    state?.meta?.launch?.simulation_speed ??
    1;

  const setSpeed = useCallback(
    (next) =>
      postControl({
        action: "set_simulation_speed",
        speed: next,
      }),
    []
  );

  return { speed, speeds: SPEEDS, setSpeed };
}
