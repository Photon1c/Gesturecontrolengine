from __future__ import annotations

import platform
import subprocess
from typing import Any


class TTSEngine:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        self._voice = str(cfg.get("voice", "default"))
        self._rate = int(cfg.get("rate", 180))

    def speak(self, text: str) -> None:
        system = platform.system().lower()
        if system.startswith("win"):
            self._speak_windows(text)
        elif system.startswith("darwin"):
            self._speak_macos(text)
        else:
            self._speak_linux(text)

    def _speak_windows(self, text: str) -> None:
        import pyttsx3

        engine = pyttsx3.init()
        engine.setProperty("rate", self._rate)
        voices = engine.getProperty("voices")
        for v in voices:
            if "british" in v.name.lower() or "uk" in v.name.lower():
                engine.setProperty("voice", v.id)
                break
        engine.say(text)
        engine.runAndWait()

    def _speak_macos(self, text: str) -> None:
        voice = self._voice
        subprocess.run(["say", "-v", voice, text], check=False)

    def _speak_linux(self, text: str) -> None:
        try:
            subprocess.run(["espeak", text], check=False)
        except FileNotFoundError:
            subprocess.run(["spd-say", text], check=False)

    def status(self) -> dict[str, Any]:
        return {"voice": self._voice, "rate": self._rate}
