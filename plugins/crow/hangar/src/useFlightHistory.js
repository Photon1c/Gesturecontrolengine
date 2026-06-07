import { useEffect, useRef, useState } from "react";

const MAX_TRAIL = 180;

export function useFlightHistory(state) {
  const [trail, setTrail] = useState([]);
  const lastKey = useRef("");

  useEffect(() => {
    const pos = state?.lead?.position;
    if (!pos) return;
    const key = pos.map((v) => v.toFixed(2)).join(",");
    if (key === lastKey.current) return;
    lastKey.current = key;
    setTrail((prev) => {
      const next = [...prev, { x: pos[0], y: pos[1], z: pos[2], t: Date.now() }];
      return next.length > MAX_TRAIL ? next.slice(-MAX_TRAIL) : next;
    });
  }, [state]);

  return trail;
}
