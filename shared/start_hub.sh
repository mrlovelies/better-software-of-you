#!/usr/bin/env bash
# start_hub.sh — Start the SoY Hub server.
#
# Usage:
#   bash shared/start_hub.sh          # Start on default port (8787)
#   bash shared/start_hub.sh 9090     # Start on custom port
#   bash shared/start_hub.sh --daemon # Start as background daemon
#
# Detects platform (macOS/Linux) and sets paths accordingly.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PORT="${1:-8787}"
DAEMON=false

if [[ "${1:-}" == "--daemon" ]]; then
    DAEMON=true
    PORT="${2:-8787}"
fi

export CLAUDE_PLUGIN_ROOT="$PLUGIN_ROOT"

# Check if server is already running
if curl -sf "http://localhost:${PORT}/health" >/dev/null 2>&1; then
    echo "Hub already running on port $PORT"
    exit 0
fi

echo "Starting SoY Hub on port $PORT..."
echo "  Codebase: $PLUGIN_ROOT"
echo "  Hub dist: $PLUGIN_ROOT/hub/dist/"

if $DAEMON; then
    nohup python3 "$PLUGIN_ROOT/shared/soy_server.py" "$PORT" >> /tmp/soy-hub.log 2>&1 &
    echo "  PID: $!"
    echo "  Log: /tmp/soy-hub.log"
    # Wait for startup
    for i in 1 2 3 4 5 6; do
        sleep 0.5
        if curl -sf "http://localhost:${PORT}/health" >/dev/null 2>&1; then
            echo "Hub running at http://localhost:${PORT}"
            exit 0
        fi
    done
    echo "Warning: server started but health check not responding yet"
else
    python3 "$PLUGIN_ROOT/shared/soy_server.py" "$PORT"
fi
