# Gesturecontrolengine (Windows-First Edge Sensor MVP)

MediaPipe-based edge sensor app that runs on **Windows**, uses the webcam locally, and sends **metadata-only events** to a VPS endpoint for policy-based workflow triggering.

## Safety and MVP Scope

- Camera processing happens locally on the Windows edge node.
- **No raw image/video frames are sent** to the VPS.
- Edge node emits events only (presence, gestures, heartbeat).
- VPS is responsible for policy checks and workflow triggering.
- MVP trigger target: **`zeroclaw_smoke` only**.

Out of scope for this MVP:

- Raw video streaming to VPS
- Multi-camera routing
- Destructive workflow actions
- Face identity recognition / OCR
- Websocket transport

---

## File Structure

- `sensor_engine.py` — main loop, camera mode, dry-run, test-event mode
- `presence_detector.py` — debounced presence state detection
- `gesture_detector.py` — deliberate gesture detection with cooldowns
- `event_client.py` — authenticated HTTP event transport + retry + sequence
- `vps_ingestion.py` — VPS ingestion + policy gate + workflow trigger guard
- `monitor_ingestion.py` — lightweight decision-log monitoring utility
- `config.json` — all thresholds, endpoint, auth, and logging paths
- `requirements.txt` — Python dependencies
- `vps_config.json` — VPS policy/auth/runtime configuration
- `requirements-vps.txt` — VPS-only dependencies
- `AGENT_MONITORING.md` — runbook for the monitoring agent
- `deploy/systemd/conferenceroom-sensor-ingestion.service` — default systemd unit
- `scripts/install_systemd_service.sh` — systemd installer/generator script

---

## Quick Start (Windows)

### 1) Python setup

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

### 2) Configure endpoint + auth

Edit `config.json`:

- `transport.endpoint` (example: `https://<vps>/conferenceroom/sensors/event`)
- `transport.auth.type` (`bearer` or `shared_secret`)
- `transport.auth.token` or `transport.auth.secret`
- `sensor.sensor_id` (example: `desk_cam_1`)

### 3) Dry-run first (no network sends)

```powershell
python sensor_engine.py --dry-run --test-events --test-cycles 2
```

### 4) Camera live mode (with local overlay)

```powershell
python sensor_engine.py --debug-overlay
```

Press `q` in the overlay window to exit.

---

## Event Schema (Edge -> VPS)

All events include:

- `event_type`
- `sensor_id`
- `sequence` (strictly increasing per sensor)
- `ts` (ISO-8601 timestamp with timezone)
- `confidence` (0.0 - 1.0)
- `payload` (event-specific object)

### Presence state change

```json
{
  "event_type": "presence.state_changed",
  "sensor_id": "desk_cam_1",
  "sequence": 1842,
  "ts": "2026-03-05T22:10:11-08:00",
  "confidence": 0.93,
  "payload": {
    "state": "at_terminal"
  }
}
```

### Gesture detected

```json
{
  "event_type": "gesture.detected",
  "sensor_id": "desk_cam_1",
  "sequence": 1849,
  "ts": "2026-03-05T22:11:02-08:00",
  "confidence": 0.89,
  "payload": {
    "gesture": "arm_execute"
  }
}
```

### Heartbeat

```json
{
  "event_type": "sensor.heartbeat",
  "sensor_id": "desk_cam_1",
  "sequence": 1850,
  "ts": "2026-03-05T22:11:10-08:00",
  "confidence": 1.0,
  "payload": {
    "status": "ok"
  }
}
```

---

## Presence Sensor Behavior (MVP)

States:

- `at_terminal`
- `away`
- `resting`
- `asleep`
- `unknown`

Behavior:

- Uses local MediaPipe pose/hands results
- Uses desk-zone filtering + motion heuristics
- Applies stable-duration thresholds + transition debounce
- Emits only on **state changes**
- Emits heartbeat every `sensor.heartbeat_seconds`

Default thresholds in `config.json`:

- `at_terminal_stable_seconds`: 12
- `away_stable_seconds`: 45
- `resting_stable_seconds`: 90
- `asleep_stable_seconds`: 180

---

## Gesture Sensor Behavior (MVP)

Gestures:

- `arm_execute` (arms raised above shoulders)
- `confirm_execute` (two-hand pinch confirm)
- `pause` (open palm)
- `cancel` (crossed wrists near chest)

Behavior:

- Deliberate-only heuristics
- Per-gesture frame hold (`min_hold_frames`)
- Per-gesture cooldown (`cooldown_seconds`)
- Emits once per gesture event, not every frame

---

## Execution State Machine (Policy Contract)

This state machine is the intended **VPS-side execution policy** contract:

### States

- `IDLE`
- `ARMED`
- `PAUSED` (optional branch)

### Transitions

- `IDLE --arm_execute--> ARMED`
- `ARMED --confirm_execute (within window)--> TRIGGER zeroclaw_smoke --> IDLE`
- `ARMED --timeout--> IDLE`
- `ARMED --cancel--> IDLE`
- `ARMED --pause--> PAUSED` (or IDLE by implementation choice)
- `PAUSED --pause/cancel--> IDLE`

MVP trigger must require:

1. Latest presence state is `at_terminal`
2. `arm_execute` active for this sensor/session
3. `confirm_execute` within short window (default 8s)
4. Sequence not duplicated/out-of-order
5. Action target exactly `zeroclaw_smoke`

Any failure should be rejected and logged with reason.

> `sensor_engine.py` includes a local mirror of this state machine for operator observability only; it does **not** execute workflows.

---

## Transport and Reliability

- HTTP `POST` to `/conferenceroom/sensors/event`
- Auth via bearer token or shared secret header
- Exponential backoff with bounded retries
- Sequence persisted in `logs/sequence_state.json`
- Replay/debug log in JSONL at `logs/sensor_events.jsonl`

---

## Local Logs and Observability

The app prints concise logs for:

- Presence state transitions
- Gesture detections
- Send success/failure
- Local arming state transitions

Dry-run mode:

```powershell
python sensor_engine.py --dry-run
```

Test mode (synthetic events, no camera):

```powershell
python sensor_engine.py --test-events --dry-run
```

---

## VPS Ingestion Example (curl)

```bash
curl -X POST "https://your-vps.example.com/conferenceroom/sensors/event" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "event_type": "gesture.detected",
    "sensor_id": "desk_cam_1",
    "sequence": 1849,
    "ts": "2026-03-05T22:11:02-08:00",
    "confidence": 0.89,
    "payload": { "gesture": "arm_execute" }
  }'
```

---

## VPS Ingestion Handler (Conferenceroom Policy Gate)

The VPS service receives events and applies policy before any trigger decision.

### Install (VPS)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-vps.txt
```

### Configure

Edit `vps_config.json`:

- `auth.type` + token/secret
- `policy.confirm_window_seconds`
- `workflow.allowed_workflow` (must remain `zeroclaw_smoke`)
- `workflow.trigger_mode`:
  - `log_only` (safe default)
  - `http_post` (calls configured endpoint)

### Run

```bash
python3 vps_ingestion.py --config vps_config.json
```

### Install as systemd service (recommended on VPS)

A default unit file is included at:

- `deploy/systemd/conferenceroom-sensor-ingestion.service`

An installer script is included at:

- `scripts/install_systemd_service.sh`

Example install (customize paths/user via env vars):

```bash
sudo APP_DIR=/opt/gesturecontrolengine \
  RUN_USER=ubuntu \
  RUN_GROUP=ubuntu \
  PYTHON_BIN=/opt/gesturecontrolengine/.venv/bin/python3 \
  CONFIG_PATH=/opt/gesturecontrolengine/vps_config.json \
  ./scripts/install_systemd_service.sh
```

Service operations:

```bash
sudo systemctl status conferenceroom-sensor-ingestion --no-pager
sudo systemctl restart conferenceroom-sensor-ingestion
sudo journalctl -u conferenceroom-sensor-ingestion -n 200 --no-pager
```

Endpoints:

- `POST /conferenceroom/sensors/event` (authenticated ingestion)
- `GET /conferenceroom/sensors/state` (latest per-sensor runtime state)
- `GET /healthz` (service health)

Response shape includes:

- `accepted` (bool)
- `triggered` (bool)
- `reason` (policy/logging reason)
- `sensor_state` (latest presence/arm/sequence snapshot)

### Enforced MVP policy in `vps_ingestion.py`

- Accept only supported event types and schema
- Reject payloads containing raw media-like keys (`image`, `video`, `frame`, etc.)
- Sequence dedupe/out-of-order protection per `sensor_id`
- Trigger eligibility only when:
  1. prior `arm_execute` has moved sensor to `ARMED`
  2. `confirm_execute` arrives within confirm window
  3. latest presence state is `at_terminal`
  4. allowed workflow is exactly `zeroclaw_smoke`
- All accept/reject decisions are JSONL logged

---

## Monitoring the VPS Handler

Use the monitor utility against the decision log:

```bash
python3 monitor_ingestion.py --log ./logs/vps_ingestion_decisions.jsonl --minutes 30
```

This reports:

- total accepted/rejected/triggered counts
- top policy rejection reasons
- per-sensor event volume
- use `journalctl` output for runtime exceptions/startup failures

Detailed monitoring runbook for the assistant/agent is in:

- `AGENT_MONITORING.md`

---

## Notes for PixelTroupe / Conferenceroom Integration

- Treat this app as a trusted-but-constrained edge sensor producer.
- Keep workflow execution authority on the VPS side only.
- For MVP, route accepted trigger decisions to `zeroclaw_smoke` only.
- Store last known presence state per `sensor_id` and enforce sequence dedupe.
