#!/usr/bin/env python3
"""
Platform Health — CLI entry point.

Usage:
    python3 modules/platform-health/run.py check    # Quick health check
    python3 modules/platform-health/run.py sweep    # Full nightly sweep
    python3 modules/platform-health/run.py status   # Print current health summary
"""

import importlib.util
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path.home() / ".local" / "share" / "software-of-you" / "soy.db"

# Load .env for Telegram credentials
env_file = PLUGIN_ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

# Import sibling modules (hyphenated dir name requires importlib)
_mod_dir = Path(__file__).resolve().parent

_checks_spec = importlib.util.spec_from_file_location("checks", _mod_dir / "checks.py")
checks = importlib.util.module_from_spec(_checks_spec)
_checks_spec.loader.exec_module(checks)

_repair_spec = importlib.util.spec_from_file_location("repair", _mod_dir / "repair.py")
repair = importlib.util.module_from_spec(_repair_spec)
_repair_spec.loader.exec_module(repair)


def send_telegram(message: str):
    """Send a message via Telegram."""
    import urllib.request
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    owner_id = os.environ.get("TELEGRAM_OWNER_ID", "")
    if not bot_token or not owner_id:
        return False
    try:
        payload = json.dumps({
            "chat_id": owner_id,
            "text": message,
            "parse_mode": "Markdown",
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception:
        return False


def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def log(msg: str):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def store_check(db, sweep_id, check_type, machine, result):
    """Store a check result in the database."""
    db.execute(
        """INSERT INTO health_checks (sweep_id, check_type, machine, status, details, auto_fixed, fix_details, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
        (
            sweep_id,
            check_type,
            machine,
            result["status"],
            json.dumps(result.get("details", {})),
            1 if result.get("auto_fixed") else 0,
            result.get("fix_details"),
        ),
    )


def run_check():
    """Quick health check: process monitoring, stale server, error logs."""
    log("=== Quick Health Check ===")
    machine = checks.THIS_MACHINE or "unknown"
    db = get_db()

    # Create sweep record
    cursor = db.execute(
        """INSERT INTO health_sweeps (sweep_type, machine, created_at)
           VALUES ('check', ?, datetime('now'))""",
        (machine,),
    )
    sweep_id = cursor.lastrowid

    results = []

    # Process check (Razer only)
    result = checks.check_processes()
    store_check(db, sweep_id, "processes", machine, result)
    results.append(("processes", result))
    log(f"  processes: {result['status']}")

    # If processes are down, attempt auto-repair
    if result["status"] == "error":
        details = result.get("details", {})
        missing = details.get("missing", [])
        for proc in missing:
            if "soy_server" in proc:
                fix = repair.restart_server()
                log(f"  auto-fix restart_server: {'ok' if fix['success'] else 'failed'}")
                if fix["success"]:
                    result["auto_fixed"] = True
                    result["fix_details"] = "Server restarted"
            elif "telegram_bot" in proc:
                fix = repair.restart_telegram_bot()
                log(f"  auto-fix restart_telegram_bot: {'ok' if fix['success'] else 'failed'}")
                if fix["success"]:
                    result["auto_fixed"] = True
                    result["fix_details"] = "Bot restarted"

    # Stale server check
    result = checks.check_stale_server()
    store_check(db, sweep_id, "stale_server", machine, result)
    results.append(("stale_server", result))
    log(f"  stale_server: {result['status']}")

    # Error logs
    result = checks.check_error_logs()
    store_check(db, sweep_id, "error_logs", machine, result)
    results.append(("error_logs", result))
    log(f"  error_logs: {result['status']}")

    # Update sweep totals
    total = len(results)
    passed = sum(1 for _, r in results if r["status"] == "ok")
    warnings = sum(1 for _, r in results if r["status"] == "warning")
    errors = sum(1 for _, r in results if r["status"] == "error")
    auto_fixed = sum(1 for _, r in results if r.get("auto_fixed"))

    db.execute(
        """UPDATE health_sweeps
           SET total_checks = ?, passed = ?, warnings = ?, errors = ?, auto_fixed = ?,
               summary = ?
           WHERE id = ?""",
        (total, passed, warnings, errors, auto_fixed,
         f"{passed}/{total} ok, {warnings} warnings, {errors} errors", sweep_id),
    )
    db.commit()
    db.close()

    # Alert on errors
    if errors > 0:
        issues = [f"{name}: {r['status']}" for name, r in results if r["status"] == "error"]
        alert = f"*Platform Health — Check Alert*\n\n"
        alert += "\n".join(f"- {i}" for i in issues)
        alert += f"\n\n_{datetime.now().strftime('%I:%M %p ET')}_"
        send_telegram(alert)

    log(f"  Summary: {passed}/{total} ok, {warnings} warnings, {errors} errors")


def run_sweep():
    """Full nightly sweep: all checks + DB integrity + migration drift + Syncthing."""
    log("=== Full Nightly Sweep ===")
    machine = checks.THIS_MACHINE or "unknown"
    db = get_db()

    cursor = db.execute(
        """INSERT INTO health_sweeps (sweep_type, machine, created_at)
           VALUES ('sweep', ?, datetime('now'))""",
        (machine,),
    )
    sweep_id = cursor.lastrowid

    results = []

    # All quick checks
    for name, fn in [
        ("processes", checks.check_processes),
        ("stale_server", checks.check_stale_server),
        ("error_logs", checks.check_error_logs),
    ]:
        result = fn()
        store_check(db, sweep_id, name, machine, result)
        results.append((name, result))
        log(f"  {name}: {result['status']}")

    # DB integrity
    result = checks.check_db_integrity()
    store_check(db, sweep_id, "db_integrity", machine, result)
    results.append(("db_integrity", result))
    log(f"  db_integrity: {result['status']}")

    # If integrity fails, try rerunning migrations
    if result["status"] == "error":
        fix = repair.rerun_migrations()
        log(f"  auto-fix rerun_migrations: {'ok' if fix['success'] else 'failed'}")

    # Migration count
    result = checks.check_migration_count()
    store_check(db, sweep_id, "migration_count", machine, result)
    results.append(("migration_count", result))
    log(f"  migration_count: {result['status']}")

    # If migration drift, try rerunning bootstrap
    if result["status"] == "warning":
        details = result.get("details", {})
        if not details.get("in_sync"):
            fix = repair.rerun_migrations()
            log(f"  auto-fix rerun_migrations: {'ok' if fix['success'] else 'failed'}")
            if fix["success"]:
                result["auto_fixed"] = True
                result["fix_details"] = "Migrations re-applied"

    # Syncthing
    result = checks.check_syncthing()
    store_check(db, sweep_id, "syncthing", machine, result)
    results.append(("syncthing", result))
    log(f"  syncthing: {result['status']}")

    # Update sweep totals
    total = len(results)
    passed = sum(1 for _, r in results if r["status"] == "ok")
    warnings = sum(1 for _, r in results if r["status"] == "warning")
    errors = sum(1 for _, r in results if r["status"] == "error")
    auto_fixed = sum(1 for _, r in results if r.get("auto_fixed"))

    summary_parts = []
    if errors > 0:
        summary_parts.append(f"{errors} errors")
    if warnings > 0:
        summary_parts.append(f"{warnings} warnings")
    if auto_fixed > 0:
        summary_parts.append(f"{auto_fixed} auto-fixed")
    summary_parts.append(f"{passed}/{total} ok")

    db.execute(
        """UPDATE health_sweeps
           SET total_checks = ?, passed = ?, warnings = ?, errors = ?, auto_fixed = ?,
               summary = ?
           WHERE id = ?""",
        (total, passed, warnings, errors, auto_fixed,
         ", ".join(summary_parts), sweep_id),
    )
    db.commit()
    db.close()

    # Send Telegram summary
    emoji = "ok" if errors == 0 and warnings == 0 else "warn" if errors == 0 else "err"
    msg = f"*Platform Health — Nightly Sweep*\n\n"
    msg += f"Machine: {machine}\n"
    msg += f"Results: {', '.join(summary_parts)}\n"
    if errors > 0 or warnings > 0:
        msg += "\nIssues:\n"
        for name, r in results:
            if r["status"] != "ok":
                msg += f"  - {name}: {r['status']}\n"
    msg += f"\n_{datetime.now().strftime('%A %b %d, %I:%M %p ET')}_"
    send_telegram(msg)

    log(f"  Sweep complete: {', '.join(summary_parts)}")


def show_status():
    """Print current health summary from DB."""
    db = get_db()

    # Latest sweep
    sweep = db.execute(
        "SELECT * FROM health_sweeps ORDER BY created_at DESC LIMIT 1"
    ).fetchone()

    if not sweep:
        print("No health data yet. Run 'check' or 'sweep' first.")
        db.close()
        return

    print(f"Last sweep: {sweep['sweep_type']} on {sweep['machine']} at {sweep['created_at']}")
    print(f"Summary: {sweep['summary']}")
    print()

    # Per-check latest status
    print("Current status:")
    for row in db.execute("SELECT * FROM v_health_summary ORDER BY machine, check_type").fetchall():
        status_icon = {"ok": "ok", "warning": "WARN", "error": "ERR"}.get(row["status"], "?")
        print(f"  [{status_icon}] {row['machine']}/{row['check_type']}: {row['status']}")
        if row["errors_24h"] > 0:
            print(f"      ({row['errors_24h']} errors in last 24h)")

    db.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "check":
        run_check()
    elif cmd == "sweep":
        run_sweep()
    elif cmd == "status":
        show_status()
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: run.py [check|sweep|status]")
        sys.exit(1)
