#!/usr/bin/env python3
"""
Backup Verification — Restores latest backup to temp DB and validates integrity.

Runs weekly. Compares row counts against live DB, runs integrity checks,
and sends a Telegram alert if anything is off.

Usage:
    python3 shared/backup_verify.py
"""

import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = Path.home() / ".local" / "share" / "software-of-you"
LIVE_DB = DATA_DIR / "soy.db"
BACKUP_DIR = DATA_DIR / "backups"
LOG_FILE = DATA_DIR / "backup-verify.log"

sys.path.insert(0, os.path.join(PLUGIN_ROOT, "shared"))


def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def send_telegram(msg):
    """Send alert via Telegram."""
    env_path = os.path.join(PLUGIN_ROOT, ".env")
    env = {}
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip("'\"")

    token = env.get("TELEGRAM_BOT_TOKEN")
    owner = env.get("TELEGRAM_OWNER_ID")
    if not token or not owner:
        return

    import urllib.request
    import json
    payload = json.dumps({"chat_id": owner, "text": msg, "parse_mode": "Markdown"}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload, headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


def find_latest_backup():
    """Find the most recent backup file."""
    if not BACKUP_DIR.exists():
        # Also check for inline backups
        inline = sorted(DATA_DIR.glob("soy.db.backup-*"), reverse=True)
        return inline[0] if inline else None

    backups = sorted(BACKUP_DIR.glob("*.db*"), reverse=True)
    if backups:
        return backups[0]

    inline = sorted(DATA_DIR.glob("soy.db.backup-*"), reverse=True)
    return inline[0] if inline else None


def get_table_counts(db_path):
    """Get row counts for key tables."""
    db = sqlite3.connect(str(db_path))
    tables = {}
    try:
        rows = db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        for (name,) in rows:
            if name.startswith("sqlite_"):
                continue
            try:
                count = db.execute(f"SELECT COUNT(*) FROM [{name}]").fetchone()[0]
                tables[name] = count
            except Exception:
                tables[name] = -1
    finally:
        db.close()
    return tables


def verify():
    log("=== Backup Verification Starting ===")

    # Find latest backup
    backup = find_latest_backup()
    if not backup:
        msg = "BACKUP VERIFY FAILED: No backup files found"
        log(msg)
        send_telegram(f"🔴 {msg}")
        return False

    log(f"Latest backup: {backup.name} ({backup.stat().st_size / 1024 / 1024:.1f} MB)")

    # Copy to temp location
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        shutil.copy2(str(backup), tmp_path)

        # Integrity check
        db = sqlite3.connect(tmp_path)
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        db.close()

        if integrity != "ok":
            msg = f"BACKUP VERIFY FAILED: Integrity check returned: {integrity[:200]}"
            log(msg)
            send_telegram(f"🔴 {msg}")
            return False

        log(f"Integrity check: PASSED")

        # Compare table counts
        backup_counts = get_table_counts(tmp_path)
        live_counts = get_table_counts(LIVE_DB)

        issues = []
        for table, live_count in live_counts.items():
            backup_count = backup_counts.get(table, 0)
            if live_count > 0 and backup_count == 0:
                issues.append(f"  {table}: {backup_count} in backup vs {live_count} live (MISSING DATA)")
            elif live_count > 0 and backup_count < live_count * 0.5:
                issues.append(f"  {table}: {backup_count} in backup vs {live_count} live (>50% data loss)")

        if issues:
            msg = f"BACKUP VERIFY WARNING:\n" + "\n".join(issues)
            log(msg)
            send_telegram(f"🟡 Backup verification warning:\n" + "\n".join(issues[:5]))
            return False

        # Summary
        total_tables = len(backup_counts)
        total_rows = sum(v for v in backup_counts.values() if v > 0)
        backup_age_hours = (datetime.now().timestamp() - backup.stat().st_mtime) / 3600

        log(f"Tables: {total_tables}, Rows: {total_rows}, Age: {backup_age_hours:.1f}h")

        if backup_age_hours > 26:
            msg = f"BACKUP VERIFY WARNING: Latest backup is {backup_age_hours:.0f}h old"
            log(msg)
            send_telegram(f"🟡 {msg}")

        log("=== Backup Verification PASSED ===")
        return True

    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


if __name__ == "__main__":
    verify()
