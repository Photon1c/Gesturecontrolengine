# Quickstart — Windows edge + VPS

Minimal steps to run the **desk sensor on Windows** and the **ingestion service on a VPS**, plus **how to see that they are actually linked**.

---

## 1. Windows (edge sensor)

### One-time setup

```powershell
cd D:\path\to\Gesturecontrolengine
py -3.10 -m venv .venv
.\.venv\Scripts\activate
pip install -U pip
pip install -r requirements.txt
```

**MediaPipe Tasks:** pose and hand tracking use the modern **Tasks** API (`PoseLandmarker` + `HandLandmarker`). On the first camera run, the app downloads **`.task` model files** into `./models/` (ignored by git). Use a normal **venv**; some Anaconda installs have broken MediaPipe native bindings—if you see ctypes or DLL errors, switch to `py -3.10 -m venv .venv`.

Optional **`config.json`** keys (all under a top-level `"mediapipe"` object):

| Field | Default | Purpose |
|--------|---------|---------|
| `model_dir` | `./models` (resolved from cwd) | Directory for `.task` files |
| `pose_model` | `pose_landmarker_lite.task` | Filename under `model_dir` |
| `hand_model` | `hand_landmarker.task` | Filename under `model_dir` |

Edit **`config.json`**:

| Field | Purpose |
|--------|--------|
| `transport.endpoint` | Full URL to the event API, e.g. `https://YOUR_VPS:8000/conferenceroom/sensors/event` |
| `transport.auth` | `bearer` + `token`, or `shared_secret` + `secret` — must match the VPS `vps_config.json` |
| `sensor.sensor_id` | Stable ID for this camera (e.g. `desk_cam_1`) |
| `sensor.camera_index` | Usually `0` for a single webcam; use **Scan cameras** in the GUI if unsure |

### Control panel (recommended)

```powershell
.\.venv\Scripts\activate
python sensor_engine.py --gui
```

Or: `python desktop_gui.py`

In the GUI:

1. **Check VPS (GET /healthz)** — confirms the machine can reach the VPS HTTP service (same host/port as `transport.endpoint`, path `/healthz`).
2. **Scan cameras** / **Start preview** — confirm the right device and framing.
3. **Save index to config** if you changed the camera index.
4. Leave **Dry-run** on for safe tests (no HTTP to the VPS). Turn **Dry-run** **off** to verify the real link.
5. **Run sensor with AI overlay** — opens a console (log lines) and an OpenCV window (HUD).

### Command-line equivalents

```powershell
# Synthetic events only (no camera)
python sensor_engine.py --dry-run --test-events --test-cycles 1

# Camera only (no AI, no network)
python sensor_engine.py --camera-preview

# List usable camera indices (quiet OpenCV probe)
python sensor_engine.py --list-cameras

# Full sensor + on-screen HUD (dry-run: no POST)
python sensor_engine.py --debug-overlay --dry-run

# Full sensor + HUD + POST to VPS (needs valid auth in config.json)
python sensor_engine.py --debug-overlay
```

Use **`python -u`** if you ever run without the GUI and want instant console output:

```powershell
python -u sensor_engine.py --debug-overlay
```

---

## 2. VPS (ingestion)

On the server (paths are examples — adjust to your deploy):

```bash
cd /opt/gesturecontrolengine   # or your clone path
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-vps.txt
```

Edit **`vps_config.json`**: auth must match the Windows **`config.json`** transport auth.

```bash
python3 vps_ingestion.py --config vps_config.json
```

For production, use the included **systemd** unit and `scripts/install_systemd_service.sh` (see **README.md**).

---

## 3. Visual confirmation: Windows ↔ VPS connected

Use several signals together; any one can mislead if misconfigured.

### A. GUI: “Recent sensor events”

With the sensor running, this panel tails **`logging.replay_jsonl`** (default `./logs/sensor_events.jsonl` next to `config.json`).

- **`[delivered]`** on recent lines **with dry-run off** means the edge posted successfully (HTTP success from the client’s point of view).
- **`[not_delivered]`** or errors in the console **`[SEND_FAIL]`** mean the link or auth failed — check firewall, URL, token, and that `vps_ingestion.py` is listening.

With **dry-run on**, lines still appear with **`[delivered]`** for the *local replay log only* — that does **not** prove the VPS received anything.

### B. OpenCV HUD (debug overlay)

When **`--debug-overlay`** is on, look for a line like:

`your.host:port · connected — POST OK (#N)`

- **Dry-run**: you should see **`dry-run (replay JSONL only)`** (no HTTP).
- **Live**: after the first successful POST, **`connected — POST OK (#…)`** confirms the Windows app reached the VPS endpoint with a 2xx response.

If you see **`POST FAILED — …`**, read the truncated reason on the HUD and the **`[SEND_FAIL]`** line in the console.

### C. Console (second window when launched from the GUI)

- **`[SEND_OK]`** — event accepted by HTTP (2xx).
- **`[SEND_FAIL]`** — after retries, still failing; copy the error for debugging.
- **`[DRY_RUN]`** — no network send.

The GUI launches the child process with **`python -u`** so these lines appear immediately.

### D. VPS: health + logs

From **any** machine that can reach the VPS:

```bash
curl -sS "https://YOUR_VPS:8000/healthz"
```

Watch the ingestion process output or journal for incoming events, for example:

```bash
journalctl -u conferenceroom-sensor-ingestion -f --no-pager
```

Optional local monitor on the VPS:

```bash
python3 monitor_ingestion.py --log ./logs/vps_ingestion_decisions.jsonl --minutes 15
```

---

## 4. Short checklist

| Step | What “good” looks like |
|------|-------------------------|
| GUI **Check VPS** | HTTP 200 (or your expected code) from `/healthz` |
| **Dry-run** sensor + GUI event tail | Lines append; **`[delivered]`** = written to replay log only |
| **Live** sensor (dry-run **off**) | HUD **`POST OK`**, console **`[SEND_OK]`**, GUI tail **`[delivered]`** |
| VPS | Logs show accepted events / decisions for your `sensor_id` |

---

## 5. Troubleshooting

| Symptom | Things to check |
|---------|-------------------|
| GUI health check fails | VPS down, wrong host/port, TLS/cert, firewall, or URL typo in `transport.endpoint` |
| **`POST FAILED`** / **`[not_delivered]`** | Auth token/secret mismatch, wrong path, 401/403/404 from server |
| **MediaPipe / model errors** | `pip install -r requirements.txt` (Tasks API, `mediapipe>=0.10.14`); first run downloads `./models/*.task`; use a clean venv if Anaconda breaks native bindings |
| Wrong or black camera | **`--list-cameras`**, set **`sensor.camera_index`**, try **Start preview** in the GUI |

For full architecture, event schema, and policy details, see **README.md**.
