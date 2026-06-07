"""Lightweight HTTP telemetry + static Three.js hangar (stdlib only)."""

from __future__ import annotations

import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

# Windows often maps .js → text/plain unless registered explicitly.
mimetypes.add_type("application/javascript", ".js", strict=True)
mimetypes.add_type("application/javascript", ".mjs", strict=True)

_CONTENT_TYPES = {
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}


def _content_type_for(path: Path) -> str:
    return _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")


def _is_under_root(file_path: Path, root: Path) -> bool:
    try:
        file_path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


ControlHandler = Callable[[dict[str, Any]], None]


class TelemetryServer:
    def __init__(
        self,
        host: str,
        port: int,
        web_root: Path,
        *,
        on_control: ControlHandler | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.web_root = web_root.resolve()
        self._on_control = on_control
        dist_root = self.web_root / "dist"
        if (dist_root / "index.html").is_file():
            self.serve_root = dist_root
        else:
            self.serve_root = self.web_root
        self._state: dict[str, Any] = {"status": "booting"}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._httpd: ThreadingHTTPServer | None = None

    def publish(self, state: dict[str, Any]) -> None:
        with self._lock:
            self._state = state

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def start(self) -> None:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                return

            def _cors_headers(self) -> None:
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")

            def do_OPTIONS(self) -> None:
                self.send_response(204)
                self._cors_headers()
                self.end_headers()

            def do_POST(self) -> None:
                path = urlparse(self.path).path
                if path != "/api/control":
                    self.send_error(404)
                    return
                length = int(self.headers.get("Content-Length", "0") or "0")
                raw = self.rfile.read(length) if length > 0 else b"{}"
                try:
                    payload = json.loads(raw.decode("utf-8") or "{}")
                except json.JSONDecodeError:
                    self.send_error(400, "invalid json")
                    return
                if server._on_control is not None:
                    server._on_control(payload)
                body = json.dumps({"ok": True, **payload}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._cors_headers()
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                path = urlparse(self.path).path
                if path == "/api/state":
                    payload = json.dumps(server.snapshot()).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(payload)
                    return

                if path == "/favicon.ico":
                    self.send_response(204)
                    self.end_headers()
                    return

                rel = path.lstrip("/") or "index.html"
                file_path = (server.serve_root / rel).resolve()
                if not _is_under_root(file_path, server.serve_root):
                    self.send_error(403)
                    return
                if not file_path.is_file():
                    self.send_error(404)
                    return

                self.send_response(200)
                self.send_header("Content-Type", _content_type_for(file_path))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(file_path.read_bytes())

        self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="blackwing-telemetry", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"
