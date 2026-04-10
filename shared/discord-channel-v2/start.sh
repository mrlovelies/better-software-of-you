#!/bin/bash
# Start SoY Discord v2 — Claude Code with official Discord channel plugin.
# Uses persistent session with full SoY context.
# Falls back to v1 Python bot if this fails.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
V1_SERVICE="soy-discord-bot.service"

export CLAUDE_PLUGIN_ROOT="$PLUGIN_ROOT"
export PATH="$HOME/.bun/bin:$HOME/.nvm/versions/node/v24.14.0/bin:$PATH"

cd "$PLUGIN_ROOT"

# Stop v1 bot if running (can't have two Discord bots on the same token)
if systemctl --user is-active "$V1_SERVICE" &>/dev/null; then
    echo "[v2] Stopping v1 Discord bot..."
    systemctl --user stop "$V1_SERVICE"
fi

echo "[v2] Starting Claude Code with Discord channel plugin..."
echo "[v2] Working directory: $PLUGIN_ROOT"
echo "[v2] SoY context loaded via CLAUDE.md"

# Run Claude Code with the official Discord channel plugin.
# - Runs from the SoY workspace so CLAUDE.md is loaded automatically
# - --dangerously-skip-permissions: needed for autonomous headless operation
# - --channels: activates the Discord plugin bridge
# - --name: identifies this session for /resume
# - The persistent session means full conversation memory — no history replay
# Claude Code requires a TTY for interactive/channel mode.
# Use 'script' to provide a pseudo-TTY in headless environments.
exec script -qec "claude \
    --channels plugin:discord@claude-plugins-official \
    --dangerously-skip-permissions \
    --model sonnet \
    --name soy-discord-v2 \
    --append-system-prompt 'You are running as the Discord interface for Software of You (SoY). When users message you via Discord, reply using the reply tool. Use reply_embed for structured data. Capture tasks with [TASK: title | project | priority] markers and notes with [NOTE: title | content | project]. Run bootstrap.sh if needed. You have full access to the SoY database and codebase.'" \
    /dev/null \
    2>&1 || {
    EXIT_CODE=$?
    echo "[v2] Claude Code exited with code $EXIT_CODE — falling back to v1 bot"
    systemctl --user start "$V1_SERVICE" 2>/dev/null || true
    exit $EXIT_CODE
}
