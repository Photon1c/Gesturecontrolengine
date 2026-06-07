import { useCallback, useEffect, useState } from "react";

async function postAutoMode(autoMode) {
  const res = await fetch("/api/control", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ auto_mode: autoMode }),
  });
  if (!res.ok) {
    throw new Error(`control POST failed (${res.status})`);
  }
  return res.json();
}

export function useAutoMode(state) {
  const serverAuto = state?.meta?.auto_mode !== false;
  const [localAuto, setLocalAuto] = useState(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(null);

  // Sync optimistic state when telemetry confirms server mode.
  useEffect(() => {
    if (localAuto === null) return;
    if (!pending && localAuto === serverAuto) {
      setLocalAuto(null);
    }
  }, [serverAuto, localAuto, pending]);

  const autoMode = localAuto !== null ? localAuto : serverAuto;
  const controlMode = autoMode ? "auto" : "manual";

  const setAutoMode = useCallback(
    async (nextAuto) => {
      if (pending) return;
      setError(null);
      setLocalAuto(nextAuto);
      setPending(true);
      try {
        await postAutoMode(nextAuto);
      } catch (err) {
        setLocalAuto(null);
        setError(err instanceof Error ? err.message : "toggle failed");
      } finally {
        setPending(false);
      }
    },
    [pending]
  );

  const toggle = useCallback(() => {
    setAutoMode(!autoMode);
  }, [autoMode, setAutoMode]);

  return { autoMode, controlMode, toggle, setAutoMode, pending, error };
}
