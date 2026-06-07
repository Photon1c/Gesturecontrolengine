import { useCallback, useEffect, useState } from "react";
import { roostStripMinHeight } from "./scheduleStrip.js";

const CONTENT_MIN_H = roostStripMinHeight();
const MIN_W = 280;
const DEFAULT_W = 380;
const MIN_H = CONTENT_MIN_H;
const DEFAULT_H = CONTENT_MIN_H;

function viewportMaxW() {
  return Math.max(MIN_W, Math.min(560, window.innerWidth - 40));
}

function viewportMaxH() {
  return Math.max(CONTENT_MIN_H, window.innerHeight - 36);
}

function loadBool(key, fallback) {
  try {
    const v = localStorage.getItem(key);
    return v === null ? fallback : v === "1";
  } catch {
    return fallback;
  }
}

function loadSize(key, min, max, fallback) {
  try {
    const v = parseInt(localStorage.getItem(key) || "", 10);
    return Number.isFinite(v) ? Math.min(max, Math.max(min, v)) : fallback;
  } catch {
    return fallback;
  }
}

export function useRoostStripPanel() {
  const [maxW, setMaxW] = useState(viewportMaxW);
  const [maxH, setMaxH] = useState(viewportMaxH);
  const [visible, setVisible] = useState(() => loadBool("bw_strip_visible", true));
  const [width, setWidth] = useState(() =>
    loadSize("bw_strip_width", MIN_W, viewportMaxW(), DEFAULT_W)
  );
  const [height, setHeight] = useState(() =>
    loadSize("bw_strip_height", MIN_H, viewportMaxH(), DEFAULT_H)
  );

  useEffect(() => {
    function onViewport() {
      const nextMaxW = viewportMaxW();
      const nextMaxH = viewportMaxH();
      setMaxW(nextMaxW);
      setMaxH(nextMaxH);
      setWidth((w) => Math.min(nextMaxW, Math.max(MIN_W, w)));
      setHeight((h) => Math.max(CONTENT_MIN_H, Math.min(nextMaxH, h)));
    }
    window.addEventListener("resize", onViewport);
    return () => window.removeEventListener("resize", onViewport);
  }, []);

  const toggle = useCallback(() => {
    setVisible((v) => {
      const next = !v;
      try {
        localStorage.setItem("bw_strip_visible", next ? "1" : "0");
      } catch {
        /* ignore */
      }
      return next;
    });
  }, []);

  const resize = useCallback((nextW, nextH) => {
    const w = Math.min(maxW, Math.max(MIN_W, nextW));
    const h = Math.min(maxH, Math.max(MIN_H, nextH));
    setWidth(w);
    setHeight(h);
    try {
      localStorage.setItem("bw_strip_width", String(w));
      localStorage.setItem("bw_strip_height", String(h));
    } catch {
      /* ignore */
    }
  }, [maxW, maxH]);

  useEffect(() => {
    function onKey(e) {
      if (e.key === "r" || e.key === "R") toggle();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggle]);

  return { visible, width, height, toggle, resize };
}
