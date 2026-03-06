#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-conferenceroom-sensor-ingestion}"
APP_DIR="${APP_DIR:-/opt/gesturecontrolengine}"
RUN_USER="${RUN_USER:-ubuntu}"
RUN_GROUP="${RUN_GROUP:-ubuntu}"
PYTHON_BIN="${PYTHON_BIN:-${APP_DIR}/.venv/bin/python3}"
CONFIG_PATH="${CONFIG_PATH:-${APP_DIR}/vps_config.json}"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run as root (or with sudo)." >&2
  exit 1
fi

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Python binary not found or not executable: ${PYTHON_BIN}" >&2
  exit 1
fi

if [[ ! -f "${APP_DIR}/vps_ingestion.py" ]]; then
  echo "vps_ingestion.py not found in APP_DIR: ${APP_DIR}" >&2
  exit 1
fi

if [[ ! -f "${CONFIG_PATH}" ]]; then
  echo "Config path not found: ${CONFIG_PATH}" >&2
  exit 1
fi

install -d -m 0755 "${APP_DIR}/logs"

cat > "${UNIT_PATH}" <<EOF
[Unit]
Description=Conferenceroom Sensor Ingestion Handler
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_GROUP}
WorkingDirectory=${APP_DIR}
ExecStart=${PYTHON_BIN} ${APP_DIR}/vps_ingestion.py --config ${CONFIG_PATH}
Restart=always
RestartSec=3
TimeoutStopSec=20
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=${APP_DIR}/logs
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}.service"

echo "Installed and started ${SERVICE_NAME}.service"
systemctl status "${SERVICE_NAME}.service" --no-pager --full || true
