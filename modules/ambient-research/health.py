#!/usr/bin/env python3
"""
Ambient Research Health Monitor
Checks machine health, attempts self-repair, alerts via Telegram if it can't fix things.

Runs via cron every 30 minutes on the Razer (always-on machine).
Detects which machine it's running on and uses localhost for self-checks.

Usage:
    python3 modules/ambient-research/health.py          # Run health check
    python3 modules/ambient-research/health.py --daily   # Daily summary (run at 9am)
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path.home() / ".local" / "share" / "software-of-you" / "soy.db"
LOG_FILE = Path.home() / ".local" / "share" / "software-of-you" / "health.log"

def _detect_this_machine() -> str | None:
    """Detect which machine we're running on by hostname."""
    hostname = socket.gethostname().lower()
    if "uu1kal0" in hostname or hostname == "desktop-uu1kal0" or hostname == "soy":
        return "razer"
    if "1746h58" in hostname or hostname == "desktop-1746h58" or hostname == "lucy":
        return "lucy"
    if "macbook" in hostname or "macair" in hostname:
        return "macbook"
    # Fallback: check if we can reach localhost Ollama
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags")
        with urllib.request.urlopen(req, timeout=2):
            return None  # Ollama running but can't determine which machine
    except Exception:
        return None

THIS_MACHINE = _detect_this_machine()

# Load .env
env_file = PLUGIN_ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OWNER_ID = os.environ.get("TELEGRAM_OWNER_ID", "")

MACHINES = {
    "razer": {
        "ip": "100.125.139.126",
        "ssh_user": "mrlovelies",
        "ollama_port": 11434,
        "repair_cmds": [
            "sudo bash ~/start-ollama.sh &",
            "sudo service ssh restart",
        ],
    },
    "lucy": {
        "ip": "100.74.238.16",
        "ssh_user": "mrlovelies-gaming",
        "ollama_port": 11434,
        "repair_cmds": [
            "sudo bash ~/start-ollama.sh &",
            "sudo service ssh restart",
        ],
    },
}


def log(msg: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
        # Keep log file under 1MB
        if LOG_FILE.stat().st_size > 1_000_000:
            content = LOG_FILE.read_text()
            LOG_FILE.write_text(content[-500_000:])
    except Exception:
        pass


def send_telegram(message: str):
    """Send a message to Alex via Telegram."""
    if not BOT_TOKEN or not OWNER_ID:
        log("Telegram not configured — can't send alert")
        return False

    try:
        payload = json.dumps({
            "chat_id": OWNER_ID,
            "text": message,
            "parse_mode": "Markdown",
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        log(f"Telegram send failed: {e}")
        return False


def check_ssh(machine: str) -> bool:
    """Check if we can SSH into a machine (skip if it's this machine)."""
    if machine == THIS_MACHINE:
        return True  # We're already here
    m = MACHINES[machine]
    try:
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
             f"{m['ssh_user']}@{m['ip']}", "echo ok"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0 and "ok" in result.stdout
    except (subprocess.TimeoutExpired, Exception):
        return False


def check_ollama(machine: str) -> dict:
    """Check if Ollama is responding and has models."""
    m = MACHINES[machine]
    # Use localhost if checking self
    host = "localhost" if machine == THIS_MACHINE else m["ip"]
    try:
        req = urllib.request.Request(f"http://{host}:{m['ollama_port']}/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            models = [md["name"] for md in data.get("models", [])]
            return {"online": True, "models": models}
    except Exception:
        return {"online": False, "models": []}


def attempt_repair(machine: str, issue: str) -> bool:
    """Try to fix an issue via SSH."""
    m = MACHINES[machine]
    log(f"  Attempting repair on {machine}: {issue}")

    if issue == "ollama_down":
        # Try restarting Ollama
        try:
            if machine == THIS_MACHINE:
                subprocess.Popen(
                    ["sudo", "bash", os.path.expanduser("~/start-ollama.sh")],
                    stdout=open("/tmp/ollama.log", "w"),
                    stderr=subprocess.STDOUT,
                )
            else:
                subprocess.run(
                    ["ssh", "-o", "ConnectTimeout=5",
                     f"{m['ssh_user']}@{m['ip']}",
                     "sudo bash ~/start-ollama.sh > /tmp/ollama.log 2>&1 &"],
                    capture_output=True, text=True, timeout=15,
                )
            time.sleep(5)
            # Verify
            status = check_ollama(machine)
            if status["online"]:
                log(f"  Repair successful: Ollama restarted on {machine}")
                return True
            else:
                log(f"  Repair failed: Ollama still down on {machine}")
                return False
        except Exception as e:
            log(f"  Repair failed: {e}")
            return False

    elif issue == "ollama_no_models":
        # Models missing usually means wrong OLLAMA_MODELS path
        # Not much we can auto-repair here
        log(f"  Can't auto-repair missing models on {machine}")
        return False

    elif issue == "ssh_down":
        # Can't SSH in to fix SSH... alert only
        log(f"  Can't repair SSH on {machine} — need manual intervention")
        return False

    return False


def run_health_check():
    """Run a full health check across all machines."""
    log("=== Health Check ===")
    issues = []
    all_ok = True

    for name, m in MACHINES.items():
        # Check SSH
        ssh_ok = check_ssh(name)
        if not ssh_ok:
            log(f"  {name}: SSH FAILED")
            issues.append(f"{name}: SSH unreachable — machine may be asleep or WSL stopped")
            all_ok = False
            continue

        # Check Ollama
        ollama = check_ollama(name)
        if not ollama["online"]:
            log(f"  {name}: Ollama OFFLINE — attempting repair")
            repaired = attempt_repair(name, "ollama_down")
            if not repaired:
                issues.append(f"{name}: Ollama down, auto-repair failed")
                all_ok = False
            else:
                log(f"  {name}: Ollama recovered")
        elif not ollama["models"]:
            log(f"  {name}: Ollama online but NO MODELS")
            repaired = attempt_repair(name, "ollama_no_models")
            if not repaired:
                issues.append(f"{name}: Ollama running but models not loaded")
                all_ok = False
        else:
            log(f"  {name}: OK ({len(ollama['models'])} models)")

    # Check for stale research tasks (nothing completed in 24h when there should have been)
    try:
        import sqlite3
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        stale = db.execute("""
            SELECT COUNT(*) as n FROM research_tasks
            WHERE status = 'completed' AND completed_at > datetime('now', '-24 hours')
        """).fetchone()
        if stale["n"] == 0:
            last = db.execute(
                "SELECT completed_at FROM research_tasks WHERE status='completed' ORDER BY completed_at DESC LIMIT 1"
            ).fetchone()
            last_time = last["completed_at"] if last else "never"
            issues.append(f"No research tasks completed in 24h (last: {last_time})")
            all_ok = False
        else:
            log(f"  Research tasks: {stale['n']} completed in last 24h")

        # Check for failed tasks
        failed = db.execute("""
            SELECT COUNT(*) as n FROM research_tasks
            WHERE status = 'failed' AND created_at > datetime('now', '-24 hours')
        """).fetchone()
        if failed["n"] > 0:
            issues.append(f"{failed['n']} research tasks failed in last 24h")
            all_ok = False

        db.close()
    except Exception as e:
        log(f"  DB check error: {e}")

    # Alert if issues found
    if issues:
        alert = "🔧 *Son of Anton — Health Alert*\n\n"
        for i, issue in enumerate(issues, 1):
            alert += f"{i}. {issue}\n"
        alert += f"\n_Checked at {datetime.now().strftime('%I:%M %p ET')}_"
        send_telegram(alert)
        log(f"  Alert sent: {len(issues)} issues")
    else:
        log("  All systems healthy")

    return all_ok


def run_daily_summary():
    """Send a daily health summary via Telegram."""
    log("=== Daily Summary ===")

    machine_status = []
    for name in MACHINES:
        ssh_ok = check_ssh(name)
        ollama = check_ollama(name) if ssh_ok else {"online": False, "models": []}
        status = "online" if ssh_ok and ollama["online"] else "OFFLINE"
        models = len(ollama["models"])
        machine_status.append(f"  {name}: {status} ({models} models)")

    try:
        import sqlite3
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row

        tasks_24h = db.execute(
            "SELECT COUNT(*) as n FROM research_tasks WHERE status='completed' AND completed_at > datetime('now', '-24 hours')"
        ).fetchone()
        findings_24h = db.execute(
            "SELECT COUNT(*) as n FROM research_findings WHERE created_at > datetime('now', '-24 hours')"
        ).fetchone()
        wikis = db.execute(
            "SELECT COUNT(*) as n FROM research_wikis"
        ).fetchone()
        total_words = db.execute(
            "SELECT SUM(word_count) as n FROM research_wikis"
        ).fetchone()
        failed_24h = db.execute(
            "SELECT COUNT(*) as n FROM research_tasks WHERE status='failed' AND created_at > datetime('now', '-24 hours')"
        ).fetchone()

        db.close()

        summary = (
            "📊 *Son of Anton — Daily Report*\n\n"
            "*Machines:*\n" + "\n".join(machine_status) + "\n\n"
            f"*Last 24h:*\n"
            f"  Tasks completed: {tasks_24h['n']}\n"
            f"  New findings: {findings_24h['n']}\n"
            f"  Failed tasks: {failed_24h['n']}\n\n"
            f"*Total:*\n"
            f"  Wiki documents: {wikis['n']}\n"
            f"  Total wiki words: {total_words['n'] or 0:,}\n\n"
            f"_Report time: {datetime.now().strftime('%A %b %d, %I:%M %p ET')}_"
        )

    except Exception as e:
        summary = f"📊 *Son of Anton — Daily Report*\n\n" + "\n".join(machine_status) + f"\n\nDB error: {e}"

    send_telegram(summary)
    log("  Daily summary sent")


if __name__ == "__main__":
    if "--daily" in sys.argv:
        run_daily_summary()
    else:
        run_health_check()
