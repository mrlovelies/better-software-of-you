#!/usr/bin/env python3
"""
Agent heartbeat — emit start/complete/fail events for cron jobs and pipelines.

Usage in cron scripts:
    from agent_heartbeat import agent_start, agent_complete, agent_fail

    run_id = agent_start("signal-harvester", "Full pipeline run")
    try:
        # ... do work ...
        agent_complete("signal-harvester", run_id, "Harvested 23 signals", {"signals": 23})
    except Exception as e:
        agent_fail("signal-harvester", run_id, str(e))
        raise

Or from shell:
    python3 shared/agent_heartbeat.py start signal-harvester "Full pipeline run"
    python3 shared/agent_heartbeat.py complete signal-harvester <run_id> "Done"
    python3 shared/agent_heartbeat.py fail signal-harvester <run_id> "Error message"
"""

import json
import os
import socket
import sqlite3
import sys
import uuid
from pathlib import Path

DB_PATH = Path.home() / ".local" / "share" / "software-of-you" / "soy.db"
THIS_MACHINE = socket.gethostname()


def _emit(event_type, source, summary, metadata=None):
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=30)
        conn.execute(
            """INSERT INTO events (event_type, source, summary, metadata, machine)
               VALUES (?, ?, ?, ?, ?)""",
            (event_type, source, summary,
             json.dumps(metadata) if metadata else None,
             THIS_MACHINE),
        )
        # Also update heartbeat in soy_meta
        conn.execute(
            "INSERT OR REPLACE INTO soy_meta (key, value, updated_at) VALUES (?, ?, datetime('now'))",
            (f"heartbeat:{source}", json.dumps({
                "status": "running" if event_type == "agent_started" else "idle",
                "last_event": event_type,
                "summary": summary,
            })),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[HEARTBEAT] [{source}] {event_type}: {summary} (emit failed: {e})", file=sys.stderr)


def agent_start(source: str, summary: str, metadata: dict = None) -> str:
    """Emit an agent_started event. Returns a run_id for tracking."""
    run_id = str(uuid.uuid4())[:8]
    meta = {"run_id": run_id, **(metadata or {})}
    _emit("agent_started", source, summary, meta)
    return run_id


def agent_complete(source: str, run_id: str, summary: str, metadata: dict = None):
    """Emit an agent_completed event."""
    meta = {"run_id": run_id, **(metadata or {})}
    _emit("agent_completed", source, summary, meta)


def agent_fail(source: str, run_id: str, error: str, metadata: dict = None):
    """Emit an agent_failed event."""
    meta = {"run_id": run_id, "error": error, **(metadata or {})}
    _emit("agent_failed", source, error, meta)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: agent_heartbeat.py <start|complete|fail> <source> [run_id] <summary>")
        sys.exit(1)

    action = sys.argv[1]
    source = sys.argv[2]

    if action == "start":
        summary = sys.argv[3] if len(sys.argv) > 3 else "Started"
        run_id = agent_start(source, summary)
        print(run_id)
    elif action == "complete":
        run_id = sys.argv[3] if len(sys.argv) > 3 else "unknown"
        summary = sys.argv[4] if len(sys.argv) > 4 else "Completed"
        agent_complete(source, run_id, summary)
    elif action == "fail":
        run_id = sys.argv[3] if len(sys.argv) > 3 else "unknown"
        error = sys.argv[4] if len(sys.argv) > 4 else "Unknown error"
        agent_fail(source, run_id, error)
    else:
        print(f"Unknown action: {action}")
        sys.exit(1)
