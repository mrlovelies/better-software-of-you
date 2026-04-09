#!/usr/bin/env bash
# Voice Channel — Razer install script
#
# Sets up the Python venv, installs dependencies, runs the migration,
# and installs the systemd service. Run on the Razer.
#
# Usage:
#   ssh mrlovelies@100.91.234.67 "bash ~/.software-of-you/modules/voice-channel/scripts/install-razer.sh"

set -euo pipefail

PLUGIN_ROOT="${HOME}/.software-of-you"
MODULE_DIR="${PLUGIN_ROOT}/modules/voice-channel"
VENV_DIR="${HOME}/voice-channel-env"
DB_PATH="${HOME}/.local/share/software-of-you/soy.db"
LOG_DIR="${HOME}/.local/share/software-of-you"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"

echo "=== Voice Channel installer ==="
echo "Plugin root: ${PLUGIN_ROOT}"
echo "Module dir:  ${MODULE_DIR}"
echo "Venv dir:    ${VENV_DIR}"
echo ""

# --- Step 1: verify SoY is installed ---
if [[ ! -d "${PLUGIN_ROOT}" ]]; then
    echo "ERROR: SoY plugin not found at ${PLUGIN_ROOT}"
    echo "Make sure Syncthing has replicated the SoY codebase to this machine."
    exit 1
fi

if [[ ! -d "${MODULE_DIR}" ]]; then
    echo "ERROR: voice-channel module not found at ${MODULE_DIR}"
    exit 1
fi

# --- Step 2: create venv ---
if [[ ! -d "${VENV_DIR}" ]]; then
    echo "Creating venv at ${VENV_DIR}..."
    python3 -m venv "${VENV_DIR}"
else
    echo "Venv already exists at ${VENV_DIR}"
fi

# --- Step 3: install dependencies ---
echo "Installing Python dependencies..."
"${VENV_DIR}/bin/pip" install --upgrade pip
"${VENV_DIR}/bin/pip" install -r "${MODULE_DIR}/requirements.txt"

# --- Step 4: ensure log directory exists ---
mkdir -p "${LOG_DIR}"
touch "${LOG_DIR}/voice-channel.log"

# --- Step 5: run migration ---
if [[ ! -f "${DB_PATH}" ]]; then
    echo "WARNING: SoY database not found at ${DB_PATH}"
    echo "Run the SoY bootstrap first, then re-run this installer."
    exit 1
fi

MIGRATION_FILE="${PLUGIN_ROOT}/data/migrations/058_voice_channel.sql"
if [[ ! -f "${MIGRATION_FILE}" ]]; then
    echo "ERROR: Migration 058_voice_channel.sql not found"
    exit 1
fi

echo "Running migration 058_voice_channel.sql..."
sqlite3 "${DB_PATH}" < "${MIGRATION_FILE}"

# --- Step 6: install systemd user service ---
mkdir -p "${SYSTEMD_USER_DIR}"
SERVICE_FILE="${SYSTEMD_USER_DIR}/soy-voice-channel.service"

cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=SoY Voice Channel webhook (Vapi integration)
After=network.target

[Service]
Type=simple
WorkingDirectory=${MODULE_DIR}
Environment="SOY_DB_PATH=${DB_PATH}"
Environment="VOICE_CHANNEL_LOG=${LOG_DIR}/voice-channel.log"
Environment="VOICE_CHANNEL_PORT=8790"
Environment="VOICE_CHANNEL_HOST=0.0.0.0"
ExecStart=${VENV_DIR}/bin/python -m src.server
Restart=always
RestartSec=5
StandardOutput=append:${LOG_DIR}/voice-channel.log
StandardError=append:${LOG_DIR}/voice-channel.log

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable soy-voice-channel.service

echo ""
echo "=== Install complete ==="
echo ""
echo "Next steps:"
echo "  1. Configure your Vapi credentials in voice_config:"
echo "     sqlite3 ${DB_PATH}"
echo "     UPDATE voice_config SET vapi_api_key='...', vapi_agent_id='...', phone_number='+1...' WHERE id=1;"
echo ""
echo "  2. Enable the module:"
echo "     UPDATE modules SET enabled=1 WHERE name='voice-channel';"
echo ""
echo "  3. Start the service:"
echo "     systemctl --user start soy-voice-channel"
echo ""
echo "  4. Check it's running:"
echo "     systemctl --user status soy-voice-channel"
echo "     curl http://localhost:8790/"
echo ""
echo "  5. Set up Tailscale Funnel for the public webhook URL:"
echo "     tailscale serve --bg --https=10000 http://localhost:8790"
echo "     tailscale funnel --bg 10000"
echo ""
echo "  6. Configure Vapi to point at the Funnel URL:"
echo "     https://soy-1.tail2272ce.ts.net:10000/webhook/tool"
echo ""
