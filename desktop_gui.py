"""Windows-friendly Tk control panel: pick a camera, preview in-window, launch the sensor.

Run: python desktop_gui.py
  or: python sensor_engine.py --gui
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import requests
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cv2
from PIL import Image, ImageTk

from sensor_engine import (
    load_config,
    open_video_capture,
    save_config,
    scan_camera_indices,
)

APP_DIR = Path(__file__).resolve().parent


def resolve_replay_jsonl(cfg_path_str: str) -> Path | None:
    try:
        cfg = load_config(cfg_path_str)
        rel = str(cfg.get("logging", {}).get("replay_jsonl", "logs/sensor_events.jsonl"))
        p = Path(rel)
        base = Path(cfg_path_str).resolve().parent
        return (p if p.is_absolute() else (base / p)).resolve()
    except Exception:
        return None


def tail_replay_jsonl(path: Path, n: int = 14) -> list[str]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out: list[str] = []
    for raw in lines[-n:]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            o = json.loads(raw)
            ev = o.get("event", {})
            et = ev.get("event_type", "?")
            seq = ev.get("sequence", "?")
            dlv = o.get("delivery", {}).get("delivered")
            tag = "delivered" if dlv else "not_delivered"
            out.append(f"#{seq}  {et}  [{tag}]")
        except (json.JSONDecodeError, TypeError):
            out.append(raw[:96])
    return out


def main() -> None:
    root = tk.Tk()
    root.title("Gesture Control Engine — Windows control panel")
    root.minsize(720, 680)

    config_path = tk.StringVar(value=str(APP_DIR / "config.json"))
    cam_index = tk.StringVar(value="0")
    dry_run = tk.BooleanVar(value=True)
    fullscreen = tk.BooleanVar(value=False)

    stop_preview = threading.Event()
    preview_queue: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=2)
    preview_thread_holder: list[threading.Thread | None] = [None]
    photo_holder: list[ImageTk.PhotoImage | None] = [None]

    main_f = ttk.Frame(root, padding=12)
    main_f.pack(fill=tk.BOTH, expand=True)

    ttk.Label(
        main_f,
        text=(
            "Use this panel to confirm your desk camera, update config, and start the sensor. "
            "Only working indices are listed when you scan; a single webcam is usually index 0."
        ),
        wraplength=680,
    ).pack(anchor=tk.W, pady=(0, 10))

    row_cfg = ttk.Frame(main_f)
    row_cfg.pack(fill=tk.X, pady=4)
    ttk.Label(row_cfg, text="config.json:").pack(side=tk.LEFT)
    ent_cfg = ttk.Entry(row_cfg, textvariable=config_path, width=56)
    ent_cfg.pack(side=tk.LEFT, padx=6, fill=tk.X, expand=True)

    def browse_config() -> None:
        p = filedialog.askopenfilename(
            title="Select config.json",
            initialdir=str(APP_DIR),
            filetypes=[("JSON", "*.json"), ("All", "*.*")],
        )
        if p:
            config_path.set(p)
            try_reload_index()

    ttk.Button(row_cfg, text="Browse…", command=browse_config).pack(side=tk.LEFT)

    row_cam = ttk.Frame(main_f)
    row_cam.pack(fill=tk.X, pady=8)
    ttk.Label(row_cam, text="Camera index:").pack(side=tk.LEFT)
    sp = ttk.Spinbox(row_cam, from_=0, to=9, textvariable=cam_index, width=6)
    sp.pack(side=tk.LEFT, padx=6)

    log = tk.Text(main_f, height=5, wrap=tk.WORD, state=tk.DISABLED, font=("Segoe UI", 10))
    log.pack(fill=tk.X, pady=8)

    def log_msg(msg: str) -> None:
        log.configure(state=tk.NORMAL)
        log.insert(tk.END, msg + "\n")
        log.see(tk.END)
        log.configure(state=tk.DISABLED)

    def try_reload_index() -> None:
        try:
            cfg = load_config(config_path.get())
            idx = cfg.get("sensor", {}).get("camera_index", 0)
            cam_index.set(str(int(idx)))
            log_msg(f"Loaded camera_index={idx} from config.")
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            log_msg(f"Could not load config: {e}")

    def do_scan() -> None:
        log_msg("Scanning indices 0–9 (OpenCV messages suppressed)…")
        root.update_idletasks()
        try:
            found = scan_camera_indices(10)
        except Exception as e:
            messagebox.showerror("Scan failed", str(e))
            log_msg(f"Scan error: {e}")
            return
        if found:
            log_msg(f"Usable camera_index value(s): {', '.join(str(i) for i in found)}")
            cam_index.set(str(found[0]))
        else:
            log_msg("No camera responded. Check privacy settings, drivers, and USB.")

    def save_index() -> None:
        try:
            idx = int(cam_index.get())
        except ValueError:
            messagebox.showerror("Invalid index", "Camera index must be a number 0–9.")
            return
        path = config_path.get()
        try:
            cfg = load_config(path)
            if "sensor" not in cfg:
                cfg["sensor"] = {}
            cfg["sensor"]["camera_index"] = idx
            save_config(path, cfg)
        except Exception as e:
            messagebox.showerror("Save failed", str(e))
            return
        messagebox.showinfo("Saved", f"sensor.camera_index = {idx}\n{path}")
        log_msg(f"Saved camera_index={idx} to {path}")

    def preview_worker() -> None:
        try:
            idx = int(cam_index.get())
        except ValueError:
            preview_queue.put(("error", "Invalid camera index"))
            return
        mirror = True
        w, h = 640, 480
        try:
            cfg = load_config(config_path.get())
            mirror = bool(cfg.get("sensor", {}).get("mirror_preview", True))
            w = int(cfg.get("sensor", {}).get("frame_width", 640))
            h = int(cfg.get("sensor", {}).get("frame_height", 480))
        except Exception:
            pass

        cap = open_video_capture(idx)
        if not cap.isOpened():
            preview_queue.put(("error", f"Could not open camera index {idx}"))
            return

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)

        while not stop_preview.is_set():
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            if mirror:
                frame = cv2.flip(frame, 1)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            try:
                preview_queue.put_nowait(("frame", rgb))
            except queue.Full:
                try:
                    preview_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    preview_queue.put_nowait(("frame", rgb))
                except queue.Full:
                    pass
        cap.release()

    def pump_preview() -> None:
        if not stop_preview.is_set() or not preview_queue.empty():
            try:
                while True:
                    kind, data = preview_queue.get_nowait()
                    if kind == "error":
                        messagebox.showerror("Preview", str(data))
                        stop_preview_inner()
                        return
                    if kind == "frame":
                        img = Image.fromarray(data)
                        try:
                            resample = Image.Resampling.LANCZOS
                        except AttributeError:
                            resample = Image.LANCZOS
                        img.thumbnail((640, 360), resample)
                        photo = ImageTk.PhotoImage(image=img)
                        video_label.configure(image=photo)
                        photo_holder[0] = photo
            except queue.Empty:
                pass
        if preview_thread_holder[0] is not None and preview_thread_holder[0].is_alive():
            root.after(33, pump_preview)

    def stop_preview_inner() -> None:
        stop_preview.set()
        t = preview_thread_holder[0]
        if t is not None and t.is_alive():
            t.join(timeout=2.0)
        preview_thread_holder[0] = None
        video_label.configure(image="")
        photo_holder[0] = None
        btn_stop.configure(state=tk.DISABLED)
        btn_start.configure(state=tk.NORMAL)

    def start_preview() -> None:
        stop_preview_inner()
        stop_preview.clear()
        while not preview_queue.empty():
            try:
                preview_queue.get_nowait()
            except queue.Empty:
                break
        btn_start.configure(state=tk.DISABLED)
        btn_stop.configure(state=tk.NORMAL)
        log_msg("Starting embedded preview (stop before changing index or closing app).")
        t = threading.Thread(target=preview_worker, daemon=True)
        preview_thread_holder[0] = t
        t.start()
        root.after(100, pump_preview)

    btn_row = ttk.Frame(main_f)
    btn_row.pack(fill=tk.X, pady=4)
    ttk.Button(btn_row, text="Scan cameras", command=do_scan).pack(side=tk.LEFT, padx=(0, 6))
    ttk.Button(btn_row, text="Save index to config", command=save_index).pack(side=tk.LEFT, padx=6)

    vid_f = ttk.LabelFrame(main_f, text="Live preview (in this window)", padding=8)
    vid_f.pack(fill=tk.BOTH, expand=True, pady=10)
    video_label = ttk.Label(vid_f, anchor=tk.CENTER)
    video_label.pack(fill=tk.BOTH, expand=True)

    prev_row = ttk.Frame(main_f)
    prev_row.pack(fill=tk.X)
    btn_start = ttk.Button(prev_row, text="Start preview", command=start_preview)
    btn_start.pack(side=tk.LEFT, padx=(0, 6))
    btn_stop = ttk.Button(prev_row, text="Stop preview", command=stop_preview_inner, state=tk.DISABLED)
    btn_stop.pack(side=tk.LEFT)

    events_f = ttk.LabelFrame(
        main_f,
        text="Recent sensor events (live tail of replay JSONL — confirms AI + transport)",
        padding=8,
    )
    events_f.pack(fill=tk.BOTH, expand=False, pady=10)
    events_text = tk.Text(
        events_f,
        height=7,
        wrap=tk.NONE,
        state=tk.DISABLED,
        font=("Consolas", 9),
    )
    events_text.pack(fill=tk.BOTH, expand=True)

    def poll_replay() -> None:
        rp = resolve_replay_jsonl(config_path.get())
        if rp is None:
            block = "Could not resolve logging.replay_jsonl from config."
        else:
            rows = tail_replay_jsonl(rp, 16)
            if not rows:
                block = (
                    f"Waiting for events (newest at bottom once lines appear).\n"
                    f"Log file: {rp}\n"
                    "Start the sensor; heartbeats and gestures append here."
                )
            else:
                block = "\n".join(rows)
        events_text.configure(state=tk.NORMAL)
        events_text.delete("1.0", tk.END)
        events_text.insert(tk.END, block)
        events_text.configure(state=tk.DISABLED)
        root.after(450, poll_replay)

    run_f = ttk.LabelFrame(main_f, text="Launch sensor (separate console + OpenCV window)", padding=8)
    run_f.pack(fill=tk.X, pady=10)
    ttk.Checkbutton(run_f, text="Dry-run (do not POST to VPS)", variable=dry_run).pack(anchor=tk.W)
    ttk.Checkbutton(run_f, text="Fullscreen overlay", variable=fullscreen).pack(anchor=tk.W)

    def ping_vps_health() -> None:
        try:
            cfg = load_config(config_path.get())
            ep = str(cfg.get("transport", {}).get("endpoint", "")).strip()
            if not ep:
                messagebox.showerror("Config", "transport.endpoint is missing in config.json.")
                return
            p = urlparse(ep)
            if not p.netloc:
                messagebox.showerror("Config", f"Could not parse endpoint: {ep}")
                return
            scheme = p.scheme or "https"
            health_url = urlunparse((scheme, p.netloc, "/healthz", "", "", ""))
            r = requests.get(health_url, timeout=10)
            log_msg(f"VPS health: GET {health_url} -> HTTP {r.status_code}")
            snippet = (r.text or "").strip()[:400]
            if 200 <= r.status_code < 300:
                messagebox.showinfo(
                    "VPS reachable",
                    f"HTTP {r.status_code}\n{health_url}\n\n{snippet or '(empty body)'}",
                )
            else:
                messagebox.showwarning(
                    "VPS returned non-success",
                    f"HTTP {r.status_code}\n{health_url}\n\n{snippet}",
                )
        except requests.RequestException as e:
            messagebox.showerror("VPS not reachable", str(e))
            log_msg(f"VPS health check failed: {e}")

    ttk.Button(run_f, text="Check VPS (GET /healthz)", command=ping_vps_health).pack(anchor=tk.W, pady=(4, 2))

    def run_sensor() -> None:
        script = APP_DIR / "sensor_engine.py"
        if not script.is_file():
            messagebox.showerror("Missing file", f"Not found: {script}")
            return
        cmd = [
            sys.executable,
            "-u",
            str(script),
            "--config",
            config_path.get(),
            "--debug-overlay",
        ]
        if dry_run.get():
            cmd.append("--dry-run")
        if fullscreen.get():
            cmd.append("--fullscreen")
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NEW_CONSOLE
        try:
            subprocess.Popen(cmd, cwd=str(APP_DIR), creationflags=flags)
        except Exception as e:
            messagebox.showerror("Launch failed", str(e))
            return
        log_msg(
            "Started sensor_engine (unbuffered -u) in a new console + OpenCV window. "
            "Watch the video HUD (FPS, pose, sequence) and the event tail above."
        )

    ttk.Button(run_f, text="Run sensor with AI overlay", command=run_sensor).pack(anchor=tk.W, pady=6)

    def on_close() -> None:
        stop_preview_inner()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    try_reload_index()
    root.after(200, poll_replay)
    root.mainloop()


if __name__ == "__main__":
    main()
