#!/usr/bin/env python3
"""
Build Monitor — Watches active builds for stalls, auto-restarts on timeouts,
alerts via Discord when human intervention is needed.

Runs as a cron job or daemon on the Razer.

Usage:
  python3 build_monitor.py check          # one-shot check all builds
  python3 build_monitor.py watch           # continuous monitoring (every 2 min)
  python3 build_monitor.py restart <id>    # manually restart a stalled build
"""

import sys
import os
import json
import time
import sqlite3
import subprocess
import argparse
from datetime import datetime
from pathlib import Path

PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(PLUGIN_ROOT, "data", "soy.db")
BUILDS_DIR = os.path.join(PLUGIN_ROOT, "builds")

# Stall detection thresholds
STALL_THRESHOLD_SECONDS = 300      # 5 min with no log activity = stalled
MAX_AUTO_RESTARTS = 3               # auto-restart up to 3 times
HUMAN_ALERT_AFTER_RESTARTS = 3      # alert Discord after this many restarts
CHECK_INTERVAL = 120                 # seconds between checks in watch mode

# Discord notification
ENV_PATH = os.path.join(PLUGIN_ROOT, ".env")


def load_env():
    env = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip("'\"")
    return env


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def discord_notify(message, channel_key="discord_harvest_channel"):
    """Send a Discord notification."""
    env = load_env()
    token = env.get("DISCORD_BOT_TOKEN", "")
    if not token:
        return

    db = get_db()
    row = db.execute("SELECT value FROM soy_meta WHERE key = ?", (channel_key,)).fetchone()
    db.close()
    if not row:
        return

    channel_id = row["value"]

    try:
        subprocess.run([
            "curl", "-s", "-X", "POST",
            "-H", f"Authorization: Bot {token}",
            "-H", "Content-Type: application/json",
            "-d", json.dumps({"content": message}),
            f"https://discord.com/api/v10/channels/{channel_id}/messages"
        ], capture_output=True, timeout=10)
    except Exception:
        pass


def get_build_state(build_dir):
    """Assess the current state of a build."""
    meta_path = os.path.join(build_dir, ".build-meta.json")
    if not os.path.exists(meta_path):
        return None

    with open(meta_path) as f:
        meta = json.load(f)

    state = {
        "id": os.path.basename(build_dir),
        "meta": meta,
        "status": meta.get("status", "unknown"),
        "is_active": False,
        "is_stalled": False,
        "stall_duration": 0,
        "restart_count": meta.get("auto_restart_count", 0),
        "last_activity": 0,
        "gsd_running": False,
    }

    # Check log freshness
    now = time.time()
    for log_name in ["build.log", "planning.log"]:
        log_path = os.path.join(build_dir, log_name)
        if os.path.exists(log_path):
            mtime = os.path.getmtime(log_path)
            state["last_activity"] = max(state["last_activity"], mtime)

    # Check if GSD is running
    try:
        result = subprocess.run(
            ["pgrep", "-f", "gsd"],
            capture_output=True, text=True, timeout=5
        )
        state["gsd_running"] = result.returncode == 0
    except Exception:
        pass

    # Determine if active
    if state["status"] == "building":
        if state["gsd_running"]:
            state["is_active"] = True
        elif state["last_activity"] > 0:
            stall_time = now - state["last_activity"]
            state["stall_duration"] = stall_time
            if stall_time > STALL_THRESHOLD_SECONDS:
                state["is_stalled"] = True
            else:
                state["is_active"] = True  # recently active, GSD might be between sessions

    return state


def diagnose_stall(build_dir):
    """Try to figure out WHY a build stalled."""
    reasons = []

    # Check planning log for errors
    for log_name in ["planning.log", "build.log"]:
        log_path = os.path.join(build_dir, log_name)
        if not os.path.exists(log_path):
            continue
        try:
            with open(log_path) as f:
                content = f.read()
            # Check last lines for known errors
            last_chunk = content[-5000:]
            if "No model configured" in last_chunk:
                reasons.append("no_model")
            if "Max restarts" in last_chunk:
                reasons.append("max_gsd_restarts")
            if "Timeout" in last_chunk or "timeout" in last_chunk:
                reasons.append("timeout")
            if "rate_limit" in last_chunk.lower() or "429" in last_chunk:
                reasons.append("rate_limited")
            if "budget" in last_chunk.lower() and ("halt" in last_chunk.lower() or "exceeded" in last_chunk.lower()):
                reasons.append("budget_exceeded")
            if "ENOSPC" in last_chunk or "No space" in last_chunk:
                reasons.append("disk_full")
            if "blocked" in last_chunk.lower():
                reasons.append("blocked")
        except Exception:
            pass

    return reasons if reasons else ["unknown"]


def can_auto_restart(state, reasons):
    """Determine if we should auto-restart or alert a human."""
    if state["restart_count"] >= MAX_AUTO_RESTARTS:
        return False, "max auto-restarts reached"

    # These can be auto-restarted
    auto_restartable = {"timeout", "max_gsd_restarts"}
    if set(reasons) & auto_restartable:
        return True, "auto-restartable stall"

    # These need human intervention
    human_required = {"no_model", "budget_exceeded", "disk_full", "blocked"}
    if set(reasons) & human_required:
        return False, f"needs human: {', '.join(set(reasons) & human_required)}"

    # Rate limiting — wait and retry
    if "rate_limited" in reasons:
        return True, "rate limited — will retry after cooldown"

    # Unknown — try once, then alert
    if state["restart_count"] == 0:
        return True, "unknown stall — first retry"

    return False, "unknown stall after retry — needs human"


def restart_build(build_dir, state):
    """Restart a stalled build."""
    build_id = os.path.basename(build_dir)
    meta_path = os.path.join(build_dir, ".build-meta.json")

    # Update restart count
    with open(meta_path) as f:
        meta = json.load(f)
    meta["auto_restart_count"] = state["restart_count"] + 1
    meta["last_restart_at"] = datetime.now().isoformat()
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    # Check if there's a run script
    run_script = os.path.join(PLUGIN_ROOT, "shared", f"run_{build_id.split('-')[0]}_b.sh")
    if not os.path.exists(run_script):
        run_script = os.path.join(PLUGIN_ROOT, "shared", "run_build_b.sh")

    if os.path.exists(run_script):
        subprocess.Popen(
            ["nohup", "bash", run_script],
            stdout=open("/tmp/build-monitor-restart.log", "a"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        return True
    else:
        # Generic restart via gsd_bridge
        seed_path = os.path.join(build_dir, "seed.md")
        if os.path.exists(seed_path):
            env = os.environ.copy()
            env["PATH"] = f"{os.path.expanduser('~/.nvm/versions/node/v22.22.1/bin')}:{os.path.expanduser('~/.local/bin')}:{env.get('PATH', '')}"
            subprocess.Popen(
                ["gsd", "headless", "--timeout", "7200000", "--json", "auto --yolo seed.md"],
                cwd=build_dir,
                stdout=open(os.path.join(build_dir, "build.log"), "a"),
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )
            return True

    return False


def cmd_check(args):
    """One-shot check of all builds."""
    if not os.path.exists(BUILDS_DIR):
        print("No builds directory.")
        return

    print(f"Build Monitor — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    for build_name in sorted(os.listdir(BUILDS_DIR)):
        build_dir = os.path.join(BUILDS_DIR, build_name)
        if not os.path.isdir(build_dir):
            continue

        state = get_build_state(build_dir)
        if not state:
            continue

        status_icon = "✅" if state["status"] == "success" else \
                      "🔨" if state["is_active"] else \
                      "⚠️" if state["is_stalled"] else \
                      "❌" if state["status"] == "error" else "⏸️"

        stall_info = ""
        if state["is_stalled"]:
            stall_mins = int(state["stall_duration"] / 60)
            reasons = diagnose_stall(build_dir)
            stall_info = f" — STALLED {stall_mins}m ({', '.join(reasons)})"

            can_restart, reason = can_auto_restart(state, reasons)
            if can_restart:
                stall_info += f" → AUTO-RESTARTING ({reason})"
                if restart_build(build_dir, state):
                    stall_info += " ✓"
                    discord_notify(
                        f"🔄 **Build auto-restarted:** {build_name}\n"
                        f"Reason: {reason}\n"
                        f"Restart #{state['restart_count'] + 1}/{MAX_AUTO_RESTARTS}"
                    )
                else:
                    stall_info += " ✗ restart failed"
            else:
                stall_info += f" → NEEDS HUMAN ({reason})"
                if state["restart_count"] <= HUMAN_ALERT_AFTER_RESTARTS:
                    discord_notify(
                        f"🚨 **Build needs attention:** {build_name}\n"
                        f"Status: stalled for {stall_mins} minutes\n"
                        f"Reason: {reason}\n"
                        f"Restarts: {state['restart_count']}/{MAX_AUTO_RESTARTS}\n"
                        f"Action needed: check build logs on the Razer"
                    )

        restart_info = f" (restarts: {state['restart_count']})" if state["restart_count"] > 0 else ""

        print(f"  {status_icon} {state['id']}: {state['status']}{stall_info}{restart_info}")

    print()


def cmd_watch(args):
    """Continuous monitoring loop."""
    print(f"Build Monitor — watching every {CHECK_INTERVAL}s")
    print(f"Stall threshold: {STALL_THRESHOLD_SECONDS}s")
    print(f"Max auto-restarts: {MAX_AUTO_RESTARTS}")
    print()

    while True:
        cmd_check(args)
        time.sleep(CHECK_INTERVAL)


def cmd_restart(args):
    """Manually restart a specific build."""
    build_dir = os.path.join(BUILDS_DIR, args.build_id)
    if not os.path.isdir(build_dir):
        print(f"Build not found: {args.build_id}")
        return

    state = get_build_state(build_dir)
    if not state:
        print(f"No build metadata found")
        return

    print(f"Manually restarting: {args.build_id}")
    state["restart_count"] = 0  # reset for manual restart
    if restart_build(build_dir, state):
        print("Restart initiated.")
    else:
        print("Restart failed — no run script or seed file found.")


def main():
    parser = argparse.ArgumentParser(description="Build Monitor")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("check", help="One-shot check all builds")
    subparsers.add_parser("watch", help="Continuous monitoring")

    p_restart = subparsers.add_parser("restart", help="Manually restart a build")
    p_restart.add_argument("build_id")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    {"check": cmd_check, "watch": cmd_watch, "restart": cmd_restart}[args.command](args)


if __name__ == "__main__":
    main()
