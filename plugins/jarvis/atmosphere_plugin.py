from __future__ import annotations

from typing import Any

from .plugin_base import JarvisPlugin


class AtmospherePlugin(JarvisPlugin):
    name = "atmosphere"

    def __init__(self, cfg: dict[str, Any]) -> None:
        super().__init__(cfg)
        self._hue_cfg = cfg.get("philips_hue", {})
        self._spotify_cfg = cfg.get("spotify", {})
        self._current_mode: str | None = None

    def _set_hue_lighting(self, scene: str) -> None:
        bridge_ip = self._hue_cfg.get("bridge_ip", "")
        api_key = self._hue_cfg.get("api_key", "")
        if not bridge_ip or not api_key:
            return
        try:
            import requests

            scenes_resp = requests.get(
                f"https://{bridge_ip}/api/{api_key}/scenes", timeout=5
            )
            scenes = scenes_resp.json()
            target = next(
                (
                    sid
                    for sid, s in scenes.items()
                    if s.get("name", "").lower() == scene.lower()
                ),
                None,
            )
            if target:
                for light_id in self._hue_cfg.get("light_ids", []):
                    requests.put(
                        f"https://{bridge_ip}/api/{api_key}/lights/{light_id}/state",
                        json={"on": True, "scene": target} if target else {"on": False},
                        timeout=5,
                    )
        except Exception:
            pass

    def _set_spotify_playlist(self, mode: str) -> None:
        client_id = self._spotify_cfg.get("client_id", "")
        client_secret = self._spotify_cfg.get("client_secret", "")
        playlist_map = self._spotify_cfg.get("playlists", {})
        if not client_id or not client_secret:
            return
        playlist_id = playlist_map.get(mode, "")
        if not playlist_id:
            return
        try:
            import base64
            import requests

            auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
            token_resp = requests.post(
                "https://accounts.spotify.com/api/token",
                headers={"Authorization": f"Basic {auth}"},
                data={"grant_type": "client_credentials"},
                timeout=5,
            )
            token = token_resp.json().get("access_token", "")
            if token:
                device_id = self._spotify_cfg.get("device_id", "")
                requests.put(
                    f"https://api.spotify.com/v1/me/player/play"
                    + (f"?device_id={device_id}" if device_id else ""),
                    headers={"Authorization": f"Bearer {token}"},
                    json={"context_uri": f"spotify:playlist:{playlist_id}"},
                    timeout=5,
                )
        except Exception:
            pass

    def on_gesture(self, gesture: str, confidence: float, ts: float) -> str | None:
        mode_map: dict[str, str] = {
            "focus": self._spotify_cfg.get("focus_mode", ""),
            "relax": self._spotify_cfg.get("relax_mode", ""),
            "energize": self._spotify_cfg.get("energize_mode", ""),
        }
        if gesture in mode_map:
            mode = gesture
            hue_scene = self._hue_cfg.get(f"{mode}_scene", "")
            if hue_scene:
                self._set_hue_lighting(hue_scene)
            if mode_map[mode]:
                self._set_spotify_playlist(mode)
            self._current_mode = mode
            return f"Atmosphere: set to {mode} mode"
        return None

    def on_tick(self, ts: float) -> str | None:
        return None

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "current_mode": self._current_mode,
            "hue_configured": bool(self._hue_cfg.get("bridge_ip")),
            "spotify_configured": bool(self._spotify_cfg.get("client_id")),
        }
