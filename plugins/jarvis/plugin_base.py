from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class JarvisPlugin(ABC):
    name: str = "base"

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        self._enabled = bool(cfg.get("enabled", True))

    @abstractmethod
    def on_gesture(self, gesture: str, confidence: float, ts: float) -> str | None: ...

    @abstractmethod
    def on_tick(self, ts: float) -> str | None: ...

    @abstractmethod
    def status(self) -> dict[str, Any]: ...

    @property
    def enabled(self) -> bool:
        return self._enabled
