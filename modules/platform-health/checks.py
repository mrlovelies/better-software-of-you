#!/usr/bin/env python3
"""
Platform Health — Individual check functions.
Each returns {"status": "ok"|"warning"|"error", "details": {...}}
"""

import json
import os
import socket
import sqlite3
import subprocess
import urllib.request
import urllib.error
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path.home() / ".local" / "share" / "software-of-you" / "soy.db"

MACHINES = {
    "soy-1": {
        "ip": "100.91.234.67",
        "ssh_user": "mrlovelies",
        "ssh_port": 2222,
    },
    "lucy": {
        "ip": "100.74.238.16",
        "ssh_user": "mrlovelies-gaming",
    },
    "legion": {
        "ip": "100.69.255.78",
        "ssh_user": "mrlovelies",
    },
}


def _detect_this_machine() -> str | None:
    """Detect which machine we're running on by hostname."""
    hostname = socket.gethostname().lower()
    if "uu1kal0" in hostname or hostname == "desktop-uu1kal0" or hostname == "soy" or hostname == "soy-1":
        return "soy-1"
    if "1746h58" in hostname or hostname == "desktop-1746h58" or hostname == "lucy":
        return "lucy"
    if "macbook" in hostname or "macair" in hostname:
        return "macbook"
    if "legion" in hostname:
        return "legion"
    return None


THIS_MACHINE = _detect_this_machine()


def check_db_integrity() -> dict:
    """Run PRAGMA integrity_check and foreign_key_check on the database."""
    try:
        db = sqlite3.connect(DB_PATH)
        integrity = db.execute("PRAGMA integrity_check").fetchone()
        fk_issues = db.execute("PRAGMA foreign_key_check").fetchall()
        db.close()

        if integrity[0] != "ok":
            return {
                "status": "error",
                "details": {"integrity": integrity[0], "foreign_key_issues": len(fk_issues)},
            }
        if fk_issues:
            return {
                "status": "warning",
                "details": {"integrity": "ok", "foreign_key_issues": len(fk_issues)},
            }
        return {
            "status": "ok",
            "details": {"integrity": "ok", "foreign_key_issues": 0},
        }
    except Exception as e:
        return {"status": "error", "details": {"error": str(e)}}


def check_processes() -> dict:
    """Check if soy_server.py and telegram_bot.py are running (soy-1 only)."""
    if THIS_MACHINE != "soy-1":
        return {"status": "ok", "details": {"skipped": True, "reason": "Not on soy-1"}}

    results = {}
    for proc_name in ["soy_server.py", "telegram_bot.py"]:
        try:
            result = subprocess.run(
                ["pgrep", "-f", proc_name],
                capture_output=True, text=True, timeout=5,
            )
            results[proc_name] = result.returncode == 0
        except Exception:
            results[proc_name] = False

    all_running = all(results.values())
    if all_running:
        return {"status": "ok", "details": results}

    missing = [k for k, v in results.items() if not v]
    return {
        "status": "error",
        "details": {**results, "missing": missing},
    }


def check_stale_server() -> dict:
    """Compare soy_server.py mtime vs process start time to detect stale code."""
    server_path = PLUGIN_ROOT / "shared" / "soy_server.py"
    if not server_path.exists():
        return {"status": "warning", "details": {"reason": "soy_server.py not found"}}

    try:
        file_mtime = server_path.stat().st_mtime

        result = subprocess.run(
            ["pgrep", "-f", "soy_server.py"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return {"status": "warning", "details": {"reason": "Server not running"}}

        pid = result.stdout.strip().split("\n")[0]
        # Get process start time via ps
        ps_result = subprocess.run(
            ["ps", "-p", pid, "-o", "lstart="],
            capture_output=True, text=True, timeout=5,
        )
        if ps_result.returncode != 0:
            return {"status": "warning", "details": {"reason": "Could not read process start time"}}

        from datetime import datetime
        proc_start_str = ps_result.stdout.strip()
        # macOS: "Fri 13 Mar 08:23:49 2026", Linux: "Fri Mar 13 08:23:49 2026"
        for fmt in ("%a %b %d %H:%M:%S %Y", "%a %d %b %H:%M:%S %Y"):
            try:
                proc_start = datetime.strptime(proc_start_str, fmt).timestamp()
                break
            except ValueError:
                continue
        else:
            return {"status": "warning", "details": {"error": f"Unparseable date: {proc_start_str}"}}

        if file_mtime > proc_start:
            return {
                "status": "warning",
                "details": {"stale": True, "file_newer_by_seconds": int(file_mtime - proc_start)},
            }
        return {"status": "ok", "details": {"stale": False}}
    except Exception as e:
        return {"status": "warning", "details": {"error": str(e)}}


def check_error_logs() -> dict:
    """Scan /tmp/soy.log and telegram_bot_errors table for recent errors."""
    errors_found = []

    # Check log file
    log_path = Path("/tmp/soy.log")
    if log_path.exists():
        try:
            content = log_path.read_text()
            lines = content.strip().split("\n")
            recent_errors = [l for l in lines[-50:] if "ERROR" in l.upper() or "Traceback" in l]
            if recent_errors:
                errors_found.append(f"{len(recent_errors)} error lines in /tmp/soy.log")
        except Exception:
            pass

    # Check telegram_bot_errors table
    try:
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        count = db.execute(
            "SELECT COUNT(*) as n FROM telegram_bot_errors WHERE created_at > datetime('now', '-24 hours')"
        ).fetchone()
        if count and count["n"] > 0:
            errors_found.append(f"{count['n']} telegram bot errors in last 24h")
        db.close()
    except Exception:
        pass  # Table may not exist

    if errors_found:
        return {"status": "warning", "details": {"errors": errors_found}}
    return {"status": "ok", "details": {"errors": []}}


def check_migration_count() -> dict:
    """Count migration files locally. Compare across machines via SSH if possible."""
    migration_dir = PLUGIN_ROOT / "data" / "migrations"
    local_count = len(list(migration_dir.glob("*.sql"))) if migration_dir.exists() else 0

    remote_counts = {}
    for name, m in MACHINES.items():
        if name == THIS_MACHINE:
            remote_counts[name] = local_count
            continue
        try:
            result = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
                 f"{m['ssh_user']}@{m['ip']}",
                 "ls ~/.software-of-you/data/migrations/*.sql 2>/dev/null | wc -l"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                remote_counts[name] = int(result.stdout.strip())
        except Exception:
            remote_counts[name] = None

    counts = {k: v for k, v in remote_counts.items() if v is not None}
    counts["local"] = local_count

    unique_counts = set(counts.values())
    if len(unique_counts) <= 1:
        return {"status": "ok", "details": {"counts": counts, "in_sync": True}}
    return {
        "status": "warning",
        "details": {"counts": counts, "in_sync": False},
    }


def check_syncthing() -> dict:
    """Check Syncthing REST API for folder completion status."""
    api_key = os.environ.get("SYNCTHING_API_KEY", "")
    if not api_key:
        # Try reading from Syncthing config
        config_path = Path.home() / ".config" / "syncthing" / "config.xml"
        if config_path.exists():
            try:
                import xml.etree.ElementTree as ET
                tree = ET.parse(config_path)
                gui = tree.find(".//gui/apikey")
                if gui is not None and gui.text:
                    api_key = gui.text
            except Exception:
                pass

    if not api_key:
        return {"status": "warning", "details": {"reason": "Syncthing API key not found"}}

    try:
        req = urllib.request.Request(
            "http://localhost:8384/rest/db/completion",
            headers={"X-API-Key": api_key},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            completion = data.get("completion", 0)
            if completion >= 99:
                return {"status": "ok", "details": {"completion": completion}}
            return {"status": "warning", "details": {"completion": completion}}
    except Exception as e:
        return {"status": "warning", "details": {"error": str(e)}}
