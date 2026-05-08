from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from .plugin_base import JarvisPlugin


class DevshopPlugin(JarvisPlugin):
    name = "devshop"

    def __init__(self, cfg: dict[str, Any]) -> None:
        super().__init__(cfg)
        self._watch_dirs: list[str] = cfg.get("watch_directories", [])
        self._last_commit_hashes: dict[str, str] = {}
        self._notifications: list[str] = []

    def _check_git_status(self, directory: str) -> str | None:
        if not os.path.isdir(os.path.join(directory, ".git")):
            return None
        try:
            result = subprocess.run(
                ["git", "log", "-1", "--oneline"],
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=10,
            )
            latest = result.stdout.strip()
            if not latest:
                return None
            parts = latest.split(None, 1)
            commit_hash = parts[0] if parts else ""
            msg = parts[1] if len(parts) > 1 else ""
            prev = self._last_commit_hashes.get(directory, "")
            if prev and commit_hash != prev:
                self._last_commit_hashes[directory] = commit_hash
                return f"[{Path(directory).name}] New commit: {msg}"
            if not prev:
                self._last_commit_hashes[directory] = commit_hash
            return None
        except Exception:
            return None

    def on_gesture(self, gesture: str, confidence: float, ts: float) -> str | None:
        if gesture == "status_check":
            reports: list[str] = []
            for d in self._watch_dirs:
                report = self._check_git_status(d)
                if report:
                    reports.append(report)
            if reports:
                self._notifications.extend(reports)
                return "Devshop: " + "; ".join(reports)
            return "Devshop: all repositories up to date"
        return None

    def on_tick(self, ts: float) -> str | None:
        for d in self._watch_dirs:
            self._check_git_status(d)
        return None

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "watch_directories": self._watch_dirs,
            "pending_notifications": len(self._notifications),
            "tracked_repos": list(self._last_commit_hashes.keys()),
        }
