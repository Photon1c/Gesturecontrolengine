import { useCallback } from "react";

async function postControl(payload) {
  const res = await fetch("/api/control", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    throw new Error(`launch POST failed (${res.status})`);
  }
  return res.json();
}

export function useLaunchControl(state) {
  const launch = state?.meta?.launch || {};
  const count = launch.count ?? (state?.flock || []).length ?? 3;
  const maxCrows = launch.max_crows ?? 12;

  const resetFlock = useCallback(
    (options = {}) =>
      postControl({
        action: "reset_flock",
        count: options.count ?? count,
        formation: options.formation ?? launch.formation ?? "patrol_wedge",
        preset: options.preset ?? launch.preset ?? "balanced",
      }),
    [count, launch.formation, launch.preset]
  );

  const spawnCrow = useCallback(
    (options = {}) =>
      postControl({
        action: "spawn_crow",
        role: options.role || null,
        sex: options.sex || "unknown",
      }),
    []
  );

  return { count, maxCrows, launch, resetFlock, spawnCrow };
}
