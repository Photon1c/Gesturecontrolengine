import { useEffect, useState } from "react";

export function useTelemetry(intervalMs = 33) {
  const [state, setState] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let alive = true;

    async function poll() {
      try {
        const res = await fetch("/api/state", { cache: "no-store" });
        if (!res.ok) {
          if (alive) setError(`api ${res.status}`);
          return;
        }
        const data = await res.json();
        if (alive) {
          setState(data);
          setError(null);
        }
      } catch {
        if (alive) setError("waiting for pilot…");
      }
    }

    poll();
    const id = setInterval(poll, intervalMs);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [intervalMs]);

  return { state, error };
}
