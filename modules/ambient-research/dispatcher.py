"""
Task dispatcher for the ambient research pipeline.
Queues research tasks, assigns them to machines, and records results.
"""

import json
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import ollama_client

DB_PATH = Path.home() / ".local" / "share" / "software-of-you" / "soy.db"
PLUGIN_ROOT = Path(__file__).resolve().parents[2]


def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


# --- Task Creation ---


def create_task(
    stream_id: int,
    tier: int,
    task_type: str,
    prompt: str,
    model: str = None,
    machine: str = None,
    input_data: dict = None,
) -> int:
    """Create a research task in the queue."""
    db = get_db()
    cursor = db.execute(
        """INSERT INTO research_tasks (stream_id, tier, task_type, prompt, model, machine, input_data)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (stream_id, tier, task_type, prompt, model, machine, json.dumps(input_data) if input_data else None),
    )
    task_id = cursor.lastrowid
    db.commit()
    db.close()
    return task_id


def get_pending_tasks(tier: int = None, limit: int = 10) -> list[dict]:
    """Get pending tasks, optionally filtered by tier."""
    db = get_db()
    if tier:
        rows = db.execute(
            "SELECT * FROM research_tasks WHERE status = 'pending' AND tier = ? ORDER BY scheduled_at LIMIT ?",
            (tier, limit),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM research_tasks WHERE status = 'pending' ORDER BY tier, scheduled_at LIMIT ?",
            (limit,),
        ).fetchall()
    db.close()
    return [dict(r) for r in rows]


# --- Task Execution ---


def run_ollama_task(task: dict) -> dict:
    """Execute a task against an Ollama model."""
    machine = task.get("machine") or ollama_client.pick_machine(task["tier"])
    if not machine:
        return {"error": "No available machine for tier", "task_id": task["id"]}

    model = task.get("model") or ollama_client.pick_model(machine, task["tier"])
    if not model:
        return {"error": f"No suitable model on {machine}", "task_id": task["id"]}

    # Build system prompt based on task type
    system = _system_prompt_for(task["task_type"])

    result = ollama_client.generate(
        machine=machine,
        model=model,
        prompt=task["prompt"],
        system=system,
        timeout=300.0,
    )

    return result


def run_claude_task(task: dict) -> dict:
    """Execute a task via Claude Code CLI."""
    try:
        proc = subprocess.run(
            ["claude", "-p", task["prompt"], "--no-input"],
            capture_output=True,
            text=True,
            timeout=600,
            cwd=str(PLUGIN_ROOT),
        )
        return {
            "response": proc.stdout,
            "error": proc.stderr if proc.returncode != 0 else None,
            "model": "claude-cli",
            "machine": "local",
            "duration_ms": None,
        }
    except subprocess.TimeoutExpired:
        return {"error": "Claude CLI timed out after 600s", "model": "claude-cli", "machine": "local"}
    except FileNotFoundError:
        return {"error": "Claude CLI not found in PATH", "model": "claude-cli", "machine": "local"}


def execute_task(task_id: int) -> dict:
    """Execute a single task by ID."""
    db = get_db()
    row = db.execute("SELECT * FROM research_tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        db.close()
        return {"error": f"Task {task_id} not found"}

    task = dict(row)
    db.execute(
        "UPDATE research_tasks SET status = 'running', started_at = datetime('now') WHERE id = ?",
        (task_id,),
    )
    db.commit()

    # Dispatch based on tier
    if task["tier"] == 3:
        result = run_claude_task(task)
    else:
        result = run_ollama_task(task)

    # Record result
    status = "failed" if result.get("error") else "completed"
    db.execute(
        """UPDATE research_tasks
           SET status = ?, output_data = ?, model = COALESCE(?, model), machine = COALESCE(?, machine),
               tokens_in = ?, tokens_out = ?, duration_ms = ?, error = ?, completed_at = datetime('now')
           WHERE id = ?""",
        (
            status,
            json.dumps({"response": result.get("response", ""), "eval_rate": result.get("eval_rate")}),
            result.get("model"),
            result.get("machine"),
            result.get("tokens_in"),
            result.get("tokens_out"),
            result.get("duration_ms"),
            result.get("error"),
            task_id,
        ),
    )
    db.commit()

    # If successful, store as finding
    if status == "completed" and task["task_type"] in ("web_sweep", "summarize"):
        _store_finding(db, task, result)

    db.close()
    return result


def _store_finding(db: sqlite3.Connection, task: dict, result: dict):
    """Store a successful task result as a research finding."""
    response = result.get("response", "")
    if not response.strip():
        return

    db.execute(
        """INSERT INTO research_findings (stream_id, task_id, tier, finding_type, title, content, relevance_score)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            task["stream_id"],
            task["id"],
            task["tier"],
            "insight",
            f"Tier {task['tier']} {task['task_type']} — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            response,
            None,
        ),
    )
    db.commit()


def _system_prompt_for(task_type: str) -> str:
    """Get the system prompt for a task type."""
    prompts = {
        "web_sweep": (
            "You are a research assistant. Analyze the topic and provide a structured summary of "
            "the current landscape, recent developments, key tools, and emerging trends. "
            "Be specific and cite concrete examples. Focus on actionable insights."
        ),
        "summarize": (
            "You are a research synthesizer. Take the provided findings and produce a clear, "
            "organized summary. Identify key themes, contradictions, and gaps. "
            "Flag anything that represents a shift from previous understanding."
        ),
        "wiki_update": (
            "You are a wiki editor. Given the current wiki document and new findings, "
            "produce an updated version that integrates new information naturally. "
            "Don't just append — restructure and refine. Remove outdated information. "
            "The document should read as a coherent, current reference."
        ),
        "synthesize": (
            "You are a cross-domain intelligence analyst. Look across multiple research streams "
            "and identify connections, implications, and actionable insights that wouldn't be "
            "visible within a single stream. Focus on what's useful for the projects at hand."
        ),
        "digest": (
            "You are writing a weekly intelligence digest for a developer and creative professional. "
            "This should be engaging and readable — designed to be read with coffee, not skimmed. "
            "Connect dots across research streams, highlight what shifted, what's new, and "
            "include cross-pollination insights. Include a hands-on workshop exercise tied to "
            "the reader's current project work."
        ),
    }
    return prompts.get(task_type, "You are a helpful research assistant.")


# --- Scheduling ---


def queue_tier1_tasks():
    """Check streams and queue Tier 1 tasks that are due."""
    db = get_db()
    streams = db.execute("SELECT * FROM research_streams WHERE active = 1").fetchall()

    queued = 0
    for stream in streams:
        # Check if there's a recent Tier 1 task
        last = db.execute(
            """SELECT completed_at FROM research_tasks
               WHERE stream_id = ? AND tier = 1 AND status = 'completed'
               ORDER BY completed_at DESC LIMIT 1""",
            (stream["id"],),
        ).fetchone()

        cadence_hours = stream["tier_1_cadence_hours"] or 6
        if last and last["completed_at"]:
            last_time = datetime.fromisoformat(last["completed_at"])
            if datetime.now() - last_time < timedelta(hours=cadence_hours):
                continue

        # Queue a web sweep
        keywords = json.loads(stream["keywords"]) if stream["keywords"] else [stream["name"]]
        prompt = (
            f"Research the current state of: {stream['name']}\n\n"
            f"Focus areas and keywords: {', '.join(keywords)}\n\n"
            f"Context: {stream['description']}\n\n"
            f"Provide a structured analysis of recent developments, key tools/techniques, "
            f"emerging trends, and anything that represents a meaningful shift."
        )
        create_task(stream["id"], tier=1, task_type="web_sweep", prompt=prompt)
        queued += 1

    db.close()
    return queued


def queue_tier2_tasks():
    """Check for unprocessed Tier 1 findings and queue Tier 2 summarization."""
    db = get_db()
    streams = db.execute("SELECT * FROM research_streams WHERE active = 1").fetchall()

    queued = 0
    for stream in streams:
        # Get unincorporated findings
        findings = db.execute(
            """SELECT content FROM research_findings
               WHERE stream_id = ? AND incorporated = 0
               ORDER BY created_at DESC LIMIT 10""",
            (stream["id"],),
        ).fetchall()

        if not findings:
            continue

        # Check cadence
        last = db.execute(
            """SELECT completed_at FROM research_tasks
               WHERE stream_id = ? AND tier = 2 AND status = 'completed'
               ORDER BY completed_at DESC LIMIT 1""",
            (stream["id"],),
        ).fetchone()

        cadence_hours = stream["tier_2_cadence_hours"] or 12
        if last and last["completed_at"]:
            last_time = datetime.fromisoformat(last["completed_at"])
            if datetime.now() - last_time < timedelta(hours=cadence_hours):
                continue

        # Get current wiki content
        wiki = db.execute(
            "SELECT content FROM research_wikis WHERE stream_id = ? ORDER BY updated_at DESC LIMIT 1",
            (stream["id"],),
        ).fetchone()

        findings_text = "\n\n---\n\n".join(f["content"] for f in findings)
        wiki_text = wiki["content"] if wiki else "(No existing wiki document — create the initial version.)"

        prompt = (
            f"Stream: {stream['name']}\n\n"
            f"## Current Wiki Document\n\n{wiki_text}\n\n"
            f"## New Findings to Incorporate\n\n{findings_text}\n\n"
            f"Produce an updated wiki document that integrates the new findings. "
            f"Restructure as needed — this should read as a coherent, current reference."
        )
        create_task(
            stream["id"],
            tier=2,
            task_type="wiki_update",
            prompt=prompt,
            input_data={"finding_ids": [f["content"][:50] for f in findings]},
        )
        queued += 1

        # Mark findings as incorporated
        db.execute(
            "UPDATE research_findings SET incorporated = 1 WHERE stream_id = ? AND incorporated = 0",
            (stream["id"],),
        )
        db.commit()

    db.close()
    return queued


# --- Runner ---


def run_pending(tier: int = None, limit: int = 5):
    """Run pending tasks. Called by the scheduler."""
    tasks = get_pending_tasks(tier=tier, limit=limit)
    results = []
    for task in tasks:
        print(f"  Running task {task['id']}: tier={task['tier']} type={task['task_type']}")
        result = execute_task(task["id"])
        status = "FAIL" if result.get("error") else "OK"
        rate = result.get("eval_rate", "")
        print(f"    {status} | {result.get('model', '?')}@{result.get('machine', '?')} | {rate} tok/s")
        results.append(result)
    return results
