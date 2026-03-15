#!/usr/bin/env python3
"""
Platform Health — Auto-fix functions.
Attempts recovery for common issues.
"""

import os
import subprocess
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]


def restart_server() -> dict:
    """Kill and restart soy_server.py."""
    try:
        # Kill existing
        subprocess.run(
            ["pkill", "-f", "soy_server.py"],
            capture_output=True, timeout=5,
        )

        # Restart
        log_path = Path("/tmp/soy.log")
        with open(log_path, "a") as log_f:
            subprocess.Popen(
                ["python3", str(PLUGIN_ROOT / "shared" / "soy_server.py")],
                stdout=log_f,
                stderr=subprocess.STDOUT,
                cwd=str(PLUGIN_ROOT),
            )

        return {"success": True, "action": "restart_server"}
    except Exception as e:
        return {"success": False, "action": "restart_server", "error": str(e)}


def restart_telegram_bot() -> dict:
    """Kill and restart telegram_bot.py."""
    try:
        subprocess.run(
            ["pkill", "-f", "telegram_bot.py"],
            capture_output=True, timeout=5,
        )

        log_path = Path("/tmp/telegram_bot.log")
        with open(log_path, "a") as log_f:
            subprocess.Popen(
                ["python3", str(PLUGIN_ROOT / "modules" / "telegram" / "telegram_bot.py")],
                stdout=log_f,
                stderr=subprocess.STDOUT,
                cwd=str(PLUGIN_ROOT),
            )

        return {"success": True, "action": "restart_telegram_bot"}
    except Exception as e:
        return {"success": False, "action": "restart_telegram_bot", "error": str(e)}


def rerun_migrations() -> dict:
    """Run bootstrap.sh to apply any pending migrations."""
    try:
        result = subprocess.run(
            ["bash", str(PLUGIN_ROOT / "shared" / "bootstrap.sh")],
            capture_output=True, text=True, timeout=30,
            cwd=str(PLUGIN_ROOT),
        )
        return {
            "success": result.returncode == 0,
            "action": "rerun_migrations",
            "output": result.stdout.strip(),
        }
    except Exception as e:
        return {"success": False, "action": "rerun_migrations", "error": str(e)}
