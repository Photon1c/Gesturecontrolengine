import { useEffect, useState } from "react";
import { CAMERA_MODES, MODE_ORDER } from "./cameraModes.js";

export function useCameraMode() {
  const [mode, setMode] = useState("chase");

  useEffect(() => {
    function onKey(e) {
      const entry = MODE_ORDER.find((name) => CAMERA_MODES[name].key === e.key);
      if (entry) setMode(entry);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return { mode, config: CAMERA_MODES[mode], setMode };
}
