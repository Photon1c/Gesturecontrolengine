from __future__ import annotations

import struct
import time
from typing import Any


class AudioClapDetector:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self._sample_rate = int(cfg.get("sample_rate", 16000))
        self._chunk_size = int(cfg.get("chunk_size", 1024))
        self._threshold = float(cfg.get("threshold", 0.3))
        self._min_clap_interval = float(cfg.get("min_clap_interval", 0.1))
        self._double_clap_window = float(cfg.get("double_clap_window_seconds", 1.5))
        self._stream: Any = None
        self._last_clap_time = 0.0
        self._clap_count = 0

    def _open_stream(self) -> Any:
        try:
            import pyaudio

            p = pyaudio.PyAudio()
            stream = p.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=self._sample_rate,
                input=True,
                frames_per_buffer=self._chunk_size,
            )
            return stream, p
        except Exception:
            return None, None

    def _compute_energy(self, data: bytes) -> float:
        fmt = "<" + "h" * (len(data) // 2)
        try:
            samples = struct.unpack(fmt, data)
        except Exception:
            return 0.0
        if not samples:
            return 0.0
        peak = max(abs(s) for s in samples) / 32768.0
        return peak

    def listen(self) -> str | None:
        if self._stream is None:
            self._stream, self._p = self._open_stream()
        if self._stream is None:
            return None

        try:
            data = self._stream.read(self._chunk_size, exception_on_overflow=False)
        except Exception:
            return None

        energy = self._compute_energy(data)
        now = time.time()

        if (
            energy > self._threshold
            and (now - self._last_clap_time) > self._min_clap_interval
        ):
            self._clap_count += 1
            self._last_clap_time = now
            if self._clap_count >= 2:
                if (now - self._last_clap_time) <= self._double_clap_window:
                    self._clap_count = 0
                    return "wakeup_clap"
                self._clap_count = 1
        return None

    def close(self) -> None:
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
        if hasattr(self, "_p") and self._p:
            try:
                self._p.terminate()
            except Exception:
                pass
