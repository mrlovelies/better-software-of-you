#!/usr/bin/env bash
# scheduled_sync.sh — Background sync runner for Software of You
#
# Called by launchd 3x/day (or manually via /auto-sync run).
# Syncs Gmail, Calendar, and transcripts using the MCP server's Python venv.
# Logs results to ~/.local/share/software-of-you/logs/sync.log

set -euo pipefail

# --- Resolve plugin root ---
if [[ -z "${CLAUDE_PLUGIN_ROOT:-}" ]]; then
    # Auto-detect: this script lives in <root>/shared/
    CLAUDE_PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
fi

VENV_PYTHON="${CLAUDE_PLUGIN_ROOT}/mcp-server/.venv/bin/python3"
DATA_DIR="${HOME}/.local/share/software-of-you"
LOG_DIR="${DATA_DIR}/logs"
LOG_FILE="${LOG_DIR}/sync.log"

# --- Resolve Python: prefer MCP venv, fall back to system python3 ---
if [[ -x "${VENV_PYTHON}" ]]; then
    SYNC_PYTHON="${VENV_PYTHON}"
else
    SYNC_PYTHON="$(command -v python3 || true)"
fi

# --- Ensure log directory exists ---
mkdir -p "${LOG_DIR}"

# --- Timestamp helper ---
timestamp() {
    date '+%Y-%m-%d %H:%M:%S'
}

log() {
    echo "[$(timestamp)] $*" >> "${LOG_FILE}"
}

# --- Rotate log: keep last 500 lines ---
rotate_log() {
    if [[ -f "${LOG_FILE}" ]]; then
        local lines
        lines=$(wc -l < "${LOG_FILE}")
        if (( lines > 500 )); then
            local tmp="${LOG_FILE}.tmp"
            tail -n 500 "${LOG_FILE}" > "${tmp}"
            mv "${tmp}" "${LOG_FILE}"
        fi
    fi
}

# --- Pre-flight checks ---
if [[ -z "${SYNC_PYTHON}" || ! -x "${SYNC_PYTHON}" ]]; then
    log "ERROR: No Python3 found (tried MCP venv and system python3)"
    exit 0  # Exit 0 so launchd doesn't mark the job as failed
fi

if [[ ! -f "${DATA_DIR}/soy.db" ]]; then
    log "SKIP: Database not found — run Software of You first"
    exit 0
fi

# --- Run sync via MCP Python ---
log "--- Sync started ---"

export CLAUDE_PLUGIN_ROOT

sync_result=$(${SYNC_PYTHON} -c '
import json, os, sys

plugin_root = os.environ["CLAUDE_PLUGIN_ROOT"]
sys.path.insert(0, os.path.join(plugin_root, "mcp-server", "src"))

from software_of_you.google_sync import sync_all_accounts

result = sync_all_accounts()
print(json.dumps(result))
' 2>&1) || true

if [[ -z "${sync_result}" ]]; then
    sync_result='{"status": "error", "reason": "Python process produced no output"}'
fi

log "Result: ${sync_result}"

# --- Alert on failure via Telegram ---
sync_status=$(echo "${sync_result}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null || echo "parse_error")

if [[ "${sync_status}" != "ok" && "${sync_status}" != "skipped" ]]; then
    # Load Telegram creds from .env
    ENV_FILE="${CLAUDE_PLUGIN_ROOT}/.env"
    if [[ -f "${ENV_FILE}" ]]; then
        BOT_TOKEN=$(grep '^TELEGRAM_BOT_TOKEN=' "${ENV_FILE}" | cut -d= -f2- | tr -d "'\"")
        OWNER_ID=$(grep '^TELEGRAM_OWNER_ID=' "${ENV_FILE}" | cut -d= -f2- | tr -d "'\"")
        if [[ -n "${BOT_TOKEN}" && -n "${OWNER_ID}" ]]; then
            HOSTNAME=$(hostname -s 2>/dev/null || echo "unknown")
            MSG="⚠️ SoY sync failed on ${HOSTNAME}%0AStatus: ${sync_status}%0AResult: ${sync_result:0:200}"
            curl -s "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage?chat_id=${OWNER_ID}&text=${MSG}" >/dev/null 2>&1 || true
            log "Telegram alert sent (status: ${sync_status})"
        fi
    fi
fi

log "--- Sync complete ---"

# --- Rotate log to prevent unbounded growth ---
rotate_log

exit 0
