from __future__ import annotations

import platform
import subprocess
from datetime import datetime
from typing import Any

from .plugin_base import JarvisPlugin
from .tts_engine import TTSEngine


class WakeupPlugin(JarvisPlugin):
    name = "wakeup"

    def __init__(self, cfg: dict[str, Any]) -> None:
        super().__init__(cfg)
        self.tts = TTSEngine(cfg.get("tts", {}))
        self._clap_streak = 0
        self._last_clap_ts = 0.0
        self._double_clap_window = float(cfg.get("double_clap_window_seconds", 1.5))
        self._woke_up = False

    def _detect_double_clap(self, ts: float) -> bool:
        self._clap_streak += 1
        if self._clap_streak == 1:
            self._last_clap_ts = ts
            return False
        if self._clap_streak >= 2:
            if (ts - self._last_clap_ts) <= self._double_clap_window:
                self._clap_streak = 0
                return True
            self._clap_streak = 1
            self._last_clap_ts = ts
            return False
        return False

    def _activate_monitors(self) -> None:
        system = platform.system().lower()
        if system.startswith("win"):
            subprocess.run(
                [
                    "powershell",
                    "-Command",
                    """
                Add-Type -TypeDefinition @'
                using System;
                using System.Runtime.InteropServices;
                public class Monitor {
                    [DllImport("user32.dll")]
                    public static extern int SendMessage(int hWnd, int hMsg, int wParam, int lParam);
                }
'@
                $monitors = 0xFFFF
                [Monitor]::SendMessage(-1, 0x0112, 0xF170, $monitors)
            """,
                ],
                check=False,
                capture_output=True,
            )
        elif system.startswith("darwin"):
            subprocess.run(["caffeinate", "-u", "-t", "1"], check=False)
        else:
            subprocess.run(
                ["xset", "dpms", "force", "on"], check=False, capture_output=True
            )

    def _get_weather_summary(self) -> str:
        try:
            import requests

            lat = float(self.cfg.get("latitude", 0))
            lon = float(self.cfg.get("longitude", 0))
            if lat == 0 and lon == 0:
                return ""
            r = requests.get(
                f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true",
                timeout=5,
            )
            data = r.json()
            weather = data.get("current_weather", {})
            temp = weather.get("temperature", "?")
            desc = weather.get("weathercode", "")
            return f"The current temperature is {temp} degrees Celsius."
        except Exception:
            return ""

    def _morning_briefing(self) -> str:
        now = datetime.now()
        date_str = now.strftime("%A, %B %d, %Y")
        time_str = now.strftime("%I:%M %p").lstrip("0")
        weather = self._get_weather_summary()
        parts = [f"Good morning. It is {time_str} on {date_str}."]
        if weather:
            parts.append(weather)
        return " ".join(parts)

    def on_gesture(self, gesture: str, confidence: float, ts: float) -> str | None:
        if gesture == "wakeup_clap" and self._detect_double_clap(ts):
            self._activate_monitors()
            briefing = self._morning_briefing()
            if self.cfg.get("vocal_readout", True):
                self.tts.speak(briefing)
            self._woke_up = True
            return f"Wakeup: monitors activated, briefing delivered"
        return None

    def on_tick(self, ts: float) -> str | None:
        self._clap_streak = 0
        return None

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "woke_up": self._woke_up,
            "double_clap_window": self._double_clap_window,
        }
