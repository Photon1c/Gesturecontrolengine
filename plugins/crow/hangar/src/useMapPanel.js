import { useCallback, useEffect, useState } from "react";

const MIN = 120;
const MAX = 320;
const DEFAULT = 168;

function loadBool(key, fallback) {
  try {
    const v = localStorage.getItem(key);
    return v === null ? fallback : v === "1";
  } catch {
    return fallback;
  }
}

function loadSize(key) {
  try {
    const v = parseInt(localStorage.getItem(key) || "", 10);
    return Number.isFinite(v) ? Math.min(MAX, Math.max(MIN, v)) : DEFAULT;
  } catch {
    return DEFAULT;
  }
}

export function useMapPanel() {
  const [visible, setVisible] = useState(() => loadBool("bw_map_visible", true));
  const [size, setSize] = useState(() => loadSize("bw_map_size"));

  const toggle = useCallback(() => {
    setVisible((v) => {
      const next = !v;
      try {
        localStorage.setItem("bw_map_visible", next ? "1" : "0");
      } catch {
        /* ignore */
      }
      return next;
    });
  }, []);

  const resize = useCallback((next) => {
    const clamped = Math.min(MAX, Math.max(MIN, next));
    setSize(clamped);
    try {
      localStorage.setItem("bw_map_size", String(clamped));
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    function onKey(e) {
      if (e.key === "m" || e.key === "M") toggle();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggle]);

  return { visible, size, toggle, resize };
}
