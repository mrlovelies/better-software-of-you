#!/usr/bin/env python3
"""
Signal Harvester — Scheduled Pipeline Runner.

Runs the full harvest → triage → notify cycle.
Designed to be called by cron on the Razer.

Usage:
  python3 pipeline_cron.py run          # full cycle: harvest + triage + competitive + notify
  python3 pipeline_cron.py harvest-only  # just harvest, no triage (faster, for frequent runs)
  python3 pipeline_cron.py notify-only   # just post current state to Discord

Recommended cron schedule (add to Razer):
  # Full pipeline run 3x daily
  0 8,14,22 * * * cd /home/mrlovelies/.software-of-you && python3 shared/pipeline_cron.py run >> /tmp/pipeline-cron.log 2>&1

  # Quick harvest every 4 hours (no triage — just accumulate signals)
  0 */4 * * * cd /home/mrlovelies/.software-of-you && python3 shared/pipeline_cron.py harvest-only >> /tmp/pipeline-cron.log 2>&1
"""

import sys
import os
import json
import sqlite3
import subprocess
import argparse
from datetime import datetime

PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(PLUGIN_ROOT, "data", "soy.db")
SHARED = os.path.join(PLUGIN_ROOT, "shared")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://100.91.234.67:11434")
OLLAMA_HOST_14B = os.environ.get("OLLAMA_HOST_14B", "http://100.74.238.16:11434")


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


def run_script(name, args_list):
    """Run a pipeline script and return success/output."""
    cmd = [sys.executable, os.path.join(SHARED, name)] + args_list
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_ROOT"] = PLUGIN_ROOT
    env["OLLAMA_HOST"] = OLLAMA_HOST
    env["OLLAMA_HOST_14B"] = OLLAMA_HOST_14B

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env)
        return result.returncode == 0, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return False, "Timed out after 600s"
    except Exception as e:
        return False, str(e)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def cmd_run(args):
    """Full pipeline cycle."""
    log("=== Signal Harvester — Scheduled Run ===")

    # Pre-run backup
    import shutil
    backup_path = DB_PATH + f".backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    try:
        # Use SQLite backup API via CLI to get a consistent snapshot
        subprocess.run(
            ["sqlite3", DB_PATH, f".backup '{backup_path}'"],
            timeout=30, capture_output=True
        )
        log(f"  Backup: {backup_path}")
        # Prune old backups — keep only 5 most recent
        import glob as _glob
        backups = sorted(_glob.glob(DB_PATH + ".backup-*"), reverse=True)
        for old in backups[5:]:
            try:
                os.remove(old)
            except OSError:
                pass
    except Exception as e:
        log(f"  Backup failed: {e} — continuing anyway")
    results = {}

    # 1. Pain-point harvest (global + targeted subreddits)
    log("Stage 1a: Pain-point harvest (global)...")
    ok, out = run_script("signal_harvester.py", ["harvest", "--global-only", "--limit", "25", "--time-filter", "week"])
    results["harvest_global"] = ok
    # Extract stored count from output
    for line in out.split("\n"):
        if "New stored:" in line:
            log(f"  {line.strip()}")

    log("Stage 1b: Pain-point harvest (subreddits)...")
    ok, out = run_script("signal_harvester.py", ["harvest", "--limit", "15", "--time-filter", "week"])
    results["harvest_subs"] = ok
    for line in out.split("\n"):
        if "New stored:" in line:
            log(f"  {line.strip()}")

    # 2. Competitive intel harvest
    log("Stage 2: Competitive intelligence harvest...")
    ok, out = run_script("competitive_intel.py", ["harvest", "--limit", "15", "--time-filter", "week"])
    results["competitive_harvest"] = ok
    for line in out.split("\n"):
        if "New stored:" in line:
            log(f"  {line.strip()}")

    # 3. Triage — filter new signals
    log("Stage 3a: Tier 1 noise filter...")
    ok, out = run_script("signal_triage.py", ["filter", "--limit", "50"])
    results["triage_t1"] = ok
    for line in out.split("\n"):
        if "Tier 1 complete:" in line:
            log(f"  {line.strip()}")

    # 4. Triage — score survivors
    log("Stage 3b: Tier 2 market scoring...")
    ok, out = run_script("signal_triage.py", ["score", "--limit", "20"])
    results["triage_t2"] = ok
    for line in out.split("\n"):
        if "Tier 2 scoring complete" in line:
            log(f"  {line.strip()}")

    # 5. Competitive analysis
    log("Stage 4: Competitive signal analysis...")
    ok, out = run_script("competitive_intel.py", ["analyze", "--limit", "15"])
    results["competitive_analyze"] = ok
    for line in out.split("\n"):
        if "Analysis complete:" in line:
            log(f"  {line.strip()}")

    # 6. Update evolution stats
    log("Stage 5: Evolution stats update...")
    ok, out = run_script("signal_evolution.py", ["update"])
    results["evolution"] = ok

    # 7. Check if we should run forecasting (once per day, if enough new data)
    db = get_db()
    last_forecast = db.execute(
        "SELECT value FROM soy_meta WHERE key = 'last_forecast_run'"
    ).fetchone()
    should_forecast = not last_forecast or last_forecast["value"][:10] != datetime.now().strftime("%Y-%m-%d")

    if should_forecast:
        log("Stage 6: Generating forecasts...")
        ok, out = run_script("signal_forecast.py", ["generate", "--mode", "creative", "--count", "3"])
        results["forecast"] = ok
        db.execute("""
            INSERT OR REPLACE INTO soy_meta (key, value, updated_at)
            VALUES ('last_forecast_run', datetime('now'), datetime('now'))
        """)
        db.commit()
    else:
        log("Stage 6: Skipping forecasts (already ran today)")

    db.close()

    # 7b. Infer rejection reasons for signals rejected without explanation
    log("Stage 7: Rejection inference...")
    ok, out = run_script("rejection_inference.py", ["run", "--limit", "20"])
    results["rejection_inference"] = ok
    for line in out.split("\n"):
        if "Inferred" in line:
            log(f"  {line.strip()}")

    # 8. Notify Discord
    log("Stage 7: Discord notifications...")
    run_script("pipeline_notify.py", ["summary"])
    run_script("pipeline_notify.py", ["signals"])
    run_script("pipeline_notify.py", ["competitive"])
    if should_forecast:
        run_script("pipeline_notify.py", ["forecasts"])

    # 9. Log the run
    db = get_db()
    db.execute("""
        INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
        VALUES ('pipeline', 0, 'scheduled_run', ?, datetime('now'))
    """, (json.dumps(results),))
    db.execute("""
        INSERT OR REPLACE INTO soy_meta (key, value, updated_at)
        VALUES ('pipeline_last_run', datetime('now'), datetime('now'))
    """)
    db.commit()
    db.close()

    success_count = sum(1 for v in results.values() if v)
    total = len(results)
    log(f"=== Pipeline complete: {success_count}/{total} stages succeeded ===")


def cmd_harvest_only(args):
    """Quick harvest — no triage, no notifications."""
    log("=== Quick Harvest ===")

    log("Pain-point harvest...")
    ok, out = run_script("signal_harvester.py", ["harvest", "--global-only", "--limit", "15", "--time-filter", "day"])
    for line in out.split("\n"):
        if "New stored:" in line:
            log(f"  {line.strip()}")

    log("Competitive harvest...")
    ok, out = run_script("competitive_intel.py", ["harvest", "--limit", "10", "--time-filter", "day"])
    for line in out.split("\n"):
        if "New stored:" in line:
            log(f"  {line.strip()}")

    log("=== Quick harvest done ===")


def cmd_notify_only(args):
    """Just post current state to Discord."""
    log("Posting to Discord...")
    run_script("pipeline_notify.py", ["summary"])
    run_script("pipeline_notify.py", ["signals"])
    run_script("pipeline_notify.py", ["competitive"])
    log("Done")


def main():
    parser = argparse.ArgumentParser(description="Signal Harvester — Scheduled Pipeline")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("run", help="Full pipeline cycle")
    subparsers.add_parser("harvest-only", help="Quick harvest, no triage")
    subparsers.add_parser("notify-only", help="Just post to Discord")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    {"run": cmd_run, "harvest-only": cmd_harvest_only, "notify-only": cmd_notify_only}[args.command](args)


if __name__ == "__main__":
    main()
