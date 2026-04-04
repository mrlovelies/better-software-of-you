#!/usr/bin/env python3
"""
Learning Module — Data gathering.
One function per source, each returns a list of context items for digest generation.
"""

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path.home() / ".local" / "share" / "software-of-you" / "soy.db"

# Import shared logging
sys.path.insert(0, str(PLUGIN_ROOT / "shared"))
try:
    from soy_logging import log_error, emit_event, get_logger
    _logger = get_logger("learning-context")
except ImportError:
    # Fallback if soy_logging not available
    def log_error(source, error, context=None):
        print(f"[ERROR] [{source}] {error}", file=sys.stderr)
    def emit_event(*a, **kw):
        pass
    import logging
    _logger = logging.getLogger("learning-context")

# Known project directories for git scanning
PROJECT_DIRS = [
    Path.home() / "wkspaces",
    Path.home() / ".software-of-you",
]


def get_db():
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    return db


def gather_handoffs(since: str) -> list[dict]:
    """Get session handoffs from the given date."""
    db = get_db()
    try:
        rows = db.execute(
            """SELECT summary, project_ids, branch, source, status, created_at
               FROM session_handoffs WHERE created_at > ?
               ORDER BY created_at DESC""",
            (since,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        _logger.warning("gather failed: %s", e)
        return []
    finally:
        db.close()


def gather_claude_sessions(since: str) -> list[dict]:
    """Scan Claude Code conversation history for recent sessions."""
    sessions = []
    since_dt = datetime.strptime(since[:19], "%Y-%m-%d %H:%M:%S")
    projects_dir = Path.home() / ".claude" / "projects"

    if not projects_dir.exists():
        return []

    for proj_dir in projects_dir.iterdir():
        if not proj_dir.is_dir():
            continue

        # Derive a readable project name from the dir name
        proj_name = proj_dir.name.replace("-home-mrlovelies-", "").replace("-", "/")

        for jsonl_path in proj_dir.glob("*.jsonl"):
            try:
                mtime = datetime.fromtimestamp(jsonl_path.stat().st_mtime)
                if mtime < since_dt:
                    continue

                user_messages = []
                tool_names = set()
                with open(jsonl_path) as f:
                    for line in f:
                        entry = json.loads(line)
                        if entry.get("type") == "user":
                            msg = entry.get("message", entry)
                            content = msg.get("content", "")
                            if isinstance(content, list):
                                for block in content:
                                    if block.get("type") == "text":
                                        user_messages.append(block["text"][:200])
                                        break
                            elif isinstance(content, str):
                                user_messages.append(content[:200])
                        elif entry.get("type") == "assistant":
                            msg = entry.get("message", entry)
                            content = msg.get("content", "")
                            if isinstance(content, list):
                                for block in content:
                                    if block.get("type") == "tool_use":
                                        tool_names.add(block.get("name", ""))

                if not user_messages:
                    continue

                sessions.append({
                    "project": proj_name,
                    "session_id": jsonl_path.stem[:8],
                    "message_count": len(user_messages),
                    "last_active": mtime.strftime("%Y-%m-%d %H:%M"),
                    "first_message": user_messages[0],
                    "tools_used": ", ".join(sorted(tool_names)[:10]) if tool_names else None,
                    "topics": " | ".join(user_messages[:5]),
                })
            except Exception as e:
                _logger.warning("gather item failed: %s", e)
                continue

    # Sort by last active, most recent first
    sessions.sort(key=lambda s: s["last_active"], reverse=True)
    return sessions[:20]


def gather_git_activity(since: str) -> list[dict]:
    """Scan git logs across known project directories."""
    commits = []
    since_date = since[:10]  # YYYY-MM-DD

    for base_dir in PROJECT_DIRS:
        if not base_dir.exists():
            continue

        # Check if base_dir itself is a git repo
        dirs_to_check = [base_dir]
        # Also check immediate subdirectories
        try:
            dirs_to_check.extend([d for d in base_dir.iterdir() if d.is_dir() and not d.name.startswith(".")])
        except Exception as e:
            _logger.warning("dir iteration failed for %s: %s", base_dir, e)

        for project_dir in dirs_to_check:
            git_dir = project_dir / ".git"
            if not git_dir.exists():
                continue
            try:
                result = subprocess.run(
                    ["git", "log", f"--since={since_date}", "--pretty=format:%H|%s|%an|%ai",
                     "--no-merges"],
                    capture_output=True, text=True, timeout=10,
                    cwd=str(project_dir),
                )
                if result.returncode == 0 and result.stdout.strip():
                    for line in result.stdout.strip().split("\n"):
                        parts = line.split("|", 3)
                        if len(parts) == 4:
                            commits.append({
                                "project": project_dir.name,
                                "hash": parts[0][:8],
                                "message": parts[1],
                                "author": parts[2],
                                "date": parts[3],
                            })
            except Exception as e:
                _logger.warning("gather item failed: %s", e)
                continue

    return commits


def gather_emails(since: str) -> list[dict]:
    """Get emails from the database."""
    db = get_db()
    try:
        rows = db.execute(
            """SELECT e.subject, e.snippet, e.direction, e.received_at,
                      c.name as contact_name
               FROM emails e
               LEFT JOIN contacts c ON c.id = e.contact_id
               WHERE e.received_at > ?
               ORDER BY e.received_at DESC
               LIMIT 30""",
            (since,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        _logger.warning("gather failed: %s", e)
        return []
    finally:
        db.close()


def gather_calendar(since: str) -> list[dict]:
    """Get calendar events."""
    db = get_db()
    try:
        rows = db.execute(
            """SELECT title, description, start_time, end_time, location,
                      attendees
               FROM calendar_events
               WHERE start_time > ? OR end_time > ?
               ORDER BY start_time
               LIMIT 20""",
            (since, since),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        _logger.warning("gather failed: %s", e)
        return []
    finally:
        db.close()


def gather_conversations(since: str) -> list[dict]:
    """Get interactions and transcripts."""
    db = get_db()
    items = []
    try:
        # Interactions
        rows = db.execute(
            """SELECT ci.interaction_type, ci.summary, ci.occurred_at,
                      c.name as contact_name
               FROM contact_interactions ci
               LEFT JOIN contacts c ON c.id = ci.contact_id
               WHERE ci.occurred_at > ?
               ORDER BY ci.occurred_at DESC
               LIMIT 20""",
            (since,),
        ).fetchall()
        items.extend([{**dict(r), "source": "interaction"} for r in rows])

        # Transcripts
        rows = db.execute(
            """SELECT title, summary, meeting_date, duration_minutes,
                      participants
               FROM transcripts WHERE meeting_date > ?
               ORDER BY meeting_date DESC
               LIMIT 10""",
            (since,),
        ).fetchall()
        items.extend([{**dict(r), "source": "transcript"} for r in rows])
    except Exception as e:
        _logger.warning("gather failed: %s", e)
    finally:
        db.close()
    return items


def gather_research(since: str) -> list[dict]:
    """Get research findings and tasks (if ambient-research module is installed)."""
    db = get_db()
    items = []
    try:
        # Check if module is installed
        mod = db.execute(
            "SELECT 1 FROM modules WHERE name = 'ambient-research' AND enabled = 1"
        ).fetchone()
        if not mod:
            return []

        # Recent findings
        rows = db.execute(
            """SELECT rf.title, rf.content, rf.finding_type, rf.relevance_score,
                      rs.name as stream_name
               FROM research_findings rf
               JOIN research_streams rs ON rs.id = rf.stream_id
               WHERE rf.created_at > ?
               ORDER BY rf.relevance_score DESC
               LIMIT 15""",
            (since,),
        ).fetchall()
        items.extend([{**dict(r), "source": "finding"} for r in rows])

        # Completed research tasks
        rows = db.execute(
            """SELECT rt.task_type, rt.tier, rt.model, rt.completed_at,
                      rs.name as stream_name
               FROM research_tasks rt
               JOIN research_streams rs ON rs.id = rt.stream_id
               WHERE rt.status = 'completed' AND rt.completed_at > ?
               ORDER BY rt.completed_at DESC
               LIMIT 10""",
            (since,),
        ).fetchall()
        items.extend([{**dict(r), "source": "research_task"} for r in rows])
    except Exception as e:
        _logger.warning("gather failed: %s", e)
    finally:
        db.close()
    return items


def gather_health(since: str) -> list[dict]:
    """Get health sweep results (if platform-health module is installed)."""
    db = get_db()
    try:
        mod = db.execute(
            "SELECT 1 FROM modules WHERE name = 'platform-health' AND enabled = 1"
        ).fetchone()
        if not mod:
            return []

        rows = db.execute(
            """SELECT sweep_type, machine, total_checks, passed, warnings,
                      errors, auto_fixed, summary, created_at
               FROM health_sweeps WHERE created_at > ?
               ORDER BY created_at DESC
               LIMIT 5""",
            (since,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        _logger.warning("gather failed: %s", e)
        return []
    finally:
        db.close()


def gather_project_activity(since: str) -> list[dict]:
    """Get project-related activity log entries."""
    db = get_db()
    try:
        rows = db.execute(
            """SELECT al.entity_type, al.action, al.details, al.created_at,
                      CASE
                        WHEN al.entity_type = 'project' THEN (SELECT name FROM projects WHERE id = al.entity_id)
                        WHEN al.entity_type = 'task' THEN (SELECT title FROM tasks WHERE id = al.entity_id)
                        ELSE NULL
                      END as entity_name
               FROM activity_log al
               WHERE al.created_at > ?
                 AND al.entity_type IN ('project', 'task', 'project_analysis_item')
               ORDER BY al.created_at DESC
               LIMIT 20""",
            (since,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        _logger.warning("gather failed: %s", e)
        return []
    finally:
        db.close()


def gather_bot_conversations(since: str) -> list[dict]:
    """Get Telegram and Discord bot conversations."""
    db = get_db()
    items = []
    try:
        # Telegram conversations
        rows = db.execute(
            """SELECT tc.role, tc.content, tc.created_at,
                      'telegram' as source
               FROM telegram_conversations tc
               WHERE tc.created_at > ?
               ORDER BY tc.created_at DESC
               LIMIT 30""",
            (since,),
        ).fetchall()
        items.extend([dict(r) for r in rows])
    except Exception as e:
        _logger.warning("gather failed: %s", e)

    try:
        # Discord conversations
        rows = db.execute(
            """SELECT dc.role, dc.content, dc.created_at,
                      dc.channel_id, 'discord' as source
               FROM discord_conversations dc
               WHERE dc.created_at > ?
               ORDER BY dc.created_at DESC
               LIMIT 30""",
            (since,),
        ).fetchall()
        items.extend([dict(r) for r in rows])
    except Exception as e:
        _logger.warning("gather failed: %s", e)

    db.close()
    return items


def gather_profile() -> dict:
    """Read the learning profile for calibration."""
    db = get_db()
    profile = {}
    try:
        rows = db.execute(
            "SELECT category, key, value FROM learning_profile"
        ).fetchall()
        for r in rows:
            cat = r["category"]
            if cat not in profile:
                profile[cat] = {}
            profile[cat][r["key"]] = r["value"]
    except Exception as e:
        _logger.warning("gather failed: %s", e)
    finally:
        db.close()
    return profile


def gather_all(since: str) -> dict:
    """Gather all context sources into a single dict."""
    return {
        "since": since,
        "handoffs": gather_handoffs(since),
        "claude_sessions": gather_claude_sessions(since),
        "git": gather_git_activity(since),
        "emails": gather_emails(since),
        "calendar": gather_calendar(since),
        "conversations": gather_conversations(since),
        "research": gather_research(since),
        "health": gather_health(since),
        "project_activity": gather_project_activity(since),
        "bot_conversations": gather_bot_conversations(since),
        "profile": gather_profile(),
    }
