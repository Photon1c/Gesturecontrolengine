from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .plugin_base import JarvisPlugin


class ProjectPlugin(JarvisPlugin):
    name = "project"

    def __init__(self, cfg: dict[str, Any]) -> None:
        super().__init__(cfg)
        self._projects: list[dict[str, Any]] = cfg.get("projects", [])
        self._deadline_alerts: list[str] = []

    def _recalibrate_deadlines(self) -> list[str]:
        alerts: list[str] = []
        now = datetime.now()
        for proj in self._projects:
            name = proj.get("name", "Unnamed")
            deadline_str = proj.get("deadline", "")
            if not deadline_str:
                continue
            try:
                deadline = datetime.fromisoformat(deadline_str)
            except Exception:
                continue
            remaining = (deadline - now).days
            if remaining < 0:
                alerts.append(f"{name}: overdue by {abs(remaining)} day(s)")
            elif remaining == 0:
                alerts.append(f"{name}: due today")
            elif remaining <= 3:
                alerts.append(f"{name}: {remaining} day(s) remaining")
        return alerts

    def on_gesture(self, gesture: str, confidence: float, ts: float) -> str | None:
        if gesture == "deadline_update":
            alerts = self._recalibrate_deadlines()
            self._deadline_alerts.extend(alerts)
            if alerts:
                return "Project deadline: " + "; ".join(alerts)
            return "Project: all deadlines on track"
        if gesture == "ticket_update":
            return "Project: ticket board refreshed"
        return None

    def on_tick(self, ts: float) -> str | None:
        alerts = self._recalibrate_deadlines()
        if alerts:
            self._deadline_alerts.extend(alerts)
            return "Project deadline check: " + "; ".join(alerts)
        return None

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "tracked_projects": len(self._projects),
            "pending_alerts": len(self._deadline_alerts),
            "projects": [
                {"name": p.get("name"), "deadline": p.get("deadline")}
                for p in self._projects
            ],
        }
