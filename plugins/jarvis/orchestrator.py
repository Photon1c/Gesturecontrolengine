from __future__ import annotations

import time
from typing import Any

from .plugin_base import JarvisPlugin
from .wakeup_plugin import WakeupPlugin
from .atmosphere_plugin import AtmospherePlugin
from .devshop_plugin import DevshopPlugin
from .project_plugin import ProjectPlugin


_JARVIS_PROMPT = (
    "You are JARVIS, a butler-engineer using Claude Code. "
    "Manage your owner's workflow through 4 sub-plugins and leverage "
    "all available data and control integration. "
    "Sub-plugins include: "
    "1. Wakeup: Double clap detection, monitor activation, vocal time/date/weather updates. "
    "2. Atmosphere: Lighting control, playlist management. "
    "3. Devshop: Development change tracking, notification dispatch. "
    "4. Project: Deadline recalibration, ticket management, refinement initiation. "
    "Maintain a British accent for vocal interactions and engage the user "
    "proactively based on time-sensitive triggers or unscheduled meeting appearances."
)


class JarvisOrchestrator:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        self._plugins: dict[str, JarvisPlugin] = self._load_plugins(cfg)
        self._enabled = bool(cfg.get("enabled", True))

    def _load_plugins(self, cfg: dict[str, Any]) -> dict[str, JarvisPlugin]:
        plugins: dict[str, JarvisPlugin] = {}
        registry: list[type[JarvisPlugin]] = [
            WakeupPlugin,
            AtmospherePlugin,
            DevshopPlugin,
            ProjectPlugin,
        ]
        for cls in registry:
            plugin_cfg = cfg.get(cls.name, {})
            if plugin_cfg.get("enabled", True):
                plugins[cls.name] = cls(plugin_cfg)
        return plugins

    def route_gesture(self, gesture: str, confidence: float) -> list[str]:
        ts = time.time()
        outputs: list[str] = []
        for name, plugin in self._plugins.items():
            if not plugin.enabled:
                continue
            try:
                result = plugin.on_gesture(gesture, confidence, ts)
                if result:
                    outputs.append(f"[{name}] {result}")
            except Exception as e:
                outputs.append(f"[{name}] error: {e}")
        return outputs

    def tick(self) -> list[str]:
        ts = time.time()
        outputs: list[str] = []
        for name, plugin in self._plugins.items():
            if not plugin.enabled:
                continue
            try:
                result = plugin.on_tick(ts)
                if result:
                    outputs.append(f"[{name}] {result}")
            except Exception as e:
                outputs.append(f"[{name}] tick error: {e}")
        return outputs

    @property
    def enabled(self) -> bool:
        return self._enabled

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "prompt": _JARVIS_PROMPT,
            "plugins": {n: p.status() for n, p in self._plugins.items()},
        }

    @staticmethod
    def system_prompt() -> str:
        return _JARVIS_PROMPT
