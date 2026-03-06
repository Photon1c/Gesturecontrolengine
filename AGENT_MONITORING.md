# VPS Ingestion Monitoring Runbook (Agent-Facing)

This runbook is for an agent/process that monitors `vps_ingestion.py`.

## Mission

Keep the ingestion service healthy and safe:

1. Service is up and reachable
2. Unauthorized/invalid requests are visible
3. Policy rejects are tracked by reason
4. Trigger events are rare, expected, and auditable
5. Any anomaly is escalated quickly

---

## What to monitor

### 1) Liveness and readiness

- `GET /healthz` should return `200` with `status=ok`.
- Check every 30-60 seconds.

### 2) Decision log health

File:

- `./logs/vps_ingestion_decisions.jsonl`

Each record should include:

- `decision.accepted`
- `decision.reason`
- `decision.triggered`
- `decision.sensor_id`
- `decision.sequence`

### 3) Policy drift / abuse signals

Alert when any of the following rise unexpectedly:

- `unauthorized`
- `duplicate_or_out_of_order_sequence`
- `raw_media_not_allowed`
- `confirm_rejected_not_armed`
- `confirm_rejected_presence_*`
- `workflow_trigger_failed:*`

### 4) Trigger rate sanity

For MVP, triggers should be low and intentional.
Unexpectedly high trigger rate = investigate immediately.

---

## Operator commands

### Quick health check

```bash
curl -sS http://127.0.0.1:8080/healthz
```

### systemd service checks

```bash
sudo systemctl status conferenceroom-sensor-ingestion --no-pager
sudo systemctl is-enabled conferenceroom-sensor-ingestion
sudo systemctl is-active conferenceroom-sensor-ingestion
sudo journalctl -u conferenceroom-sensor-ingestion -n 200 --no-pager
```

### Current per-sensor runtime state

```bash
curl -sS http://127.0.0.1:8080/conferenceroom/sensors/state
```

### Decision summary (last 30 min)

```bash
python3 monitor_ingestion.py --log ./logs/vps_ingestion_decisions.jsonl --minutes 30
```

---

## Suggested monitor cadence

- Every 1 minute:
  - `systemctl is-active conferenceroom-sensor-ingestion`
  - `/healthz`
  - `monitor_ingestion.py --minutes 5`
- Every 15 minutes:
  - `monitor_ingestion.py --minutes 30`
- Daily:
  - review all trigger decisions and confirm they map to expected usage

---

## Escalation rules

Escalate immediately if:

- health endpoint fails 3 checks in a row
- unauthorized requests spike
- raw-media payload rejections appear
- workflow trigger failures occur
- duplicate/out-of-order sequence errors spike for a single sensor

Escalation payload should include:

- time window
- top rejection reasons + counts
- affected sensor IDs
- recent triggered records

---

## Suggested prompt for a monitoring agent

Use this prompt when assigning an automated monitor:

> Monitor `vps_ingestion.py` every minute. Check `/healthz`, summarize `./logs/vps_ingestion_decisions.jsonl` via `python3 monitor_ingestion.py --minutes 5`, and alert when: health failures, unauthorized spikes, raw-media rejections, sequence replay spikes, or workflow trigger failures occur. Include counts, top reasons, and impacted sensor IDs in each alert.
