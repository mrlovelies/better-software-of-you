#!/usr/bin/env python3
"""
Kerry's Weekly SoY Brief — data-only report on Software of You development.

Gathers git commits, module changes, architecture decisions, and build-workflow
status from the last 7 days. Scoped ONLY to ~/.software-of-you/ — no client
work, no external projects.

Usage:
    python3 modules/learning/kerry_brief.py              # Print brief to stdout
    python3 modules/learning/kerry_brief.py --send       # Send via Telegram
    python3 modules/learning/kerry_brief.py --send --chat-id 12345  # Send to specific chat

Env vars:
    TELEGRAM_BOT_TOKEN   — bot token (required for --send)
    KERRY_TELEGRAM_ID    — Kerry's chat ID (falls back to TELEGRAM_OWNER_ID)
"""

import json
import os
import sqlite3
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path.home() / ".local" / "share" / "software-of-you" / "soy.db"
SOY_REPO = Path.home() / ".software-of-you"

# Load .env
env_file = PLUGIN_ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def get_db():
    db = sqlite3.connect(str(DB_PATH), timeout=30)
    db.row_factory = sqlite3.Row
    return db


def get_soy_commits(since_date: str) -> list[dict]:
    """Get git commits to ~/.software-of-you/ from the last 7 days."""
    try:
        result = subprocess.run(
            ["git", "-C", str(SOY_REPO), "log",
             f"--since={since_date}", "--format=%H|%h|%s|%an|%ai",
             "--no-merges"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return []

        commits = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("|", 4)
            if len(parts) >= 5:
                commits.append({
                    "hash": parts[0][:8],
                    "short_hash": parts[1],
                    "message": parts[2],
                    "author": parts[3],
                    "date": parts[4][:10],
                })
        return commits
    except Exception:
        return []


def get_changed_files(since_date: str) -> list[str]:
    """Get files changed in SoY repo in the last 7 days."""
    try:
        result = subprocess.run(
            ["git", "-C", str(SOY_REPO), "log",
             f"--since={since_date}", "--name-only", "--format="],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return []
        files = set()
        for line in result.stdout.strip().split("\n"):
            if line.strip():
                files.add(line.strip())
        return sorted(files)
    except Exception:
        return []


def get_new_migrations(changed_files: list[str]) -> list[str]:
    """Filter changed files for new migrations."""
    return [f for f in changed_files if f.startswith("data/migrations/")]


def get_module_changes(changed_files: list[str]) -> dict[str, list[str]]:
    """Group changed files by module."""
    modules = {}
    for f in changed_files:
        if f.startswith("modules/"):
            parts = f.split("/")
            if len(parts) >= 2:
                mod_name = parts[1]
                modules.setdefault(mod_name, []).append(f)
        elif f.startswith("skills/"):
            parts = f.split("/")
            if len(parts) >= 2:
                skill_name = parts[1]
                modules.setdefault(f"skill:{skill_name}", []).append(f)
    return modules


def get_recent_decisions(since_date: str) -> list[dict]:
    """Get architecture decisions from the decisions table."""
    try:
        db = get_db()
        rows = db.execute(
            """SELECT title, context, decision, status, created_at
               FROM decisions
               WHERE created_at >= ? AND (
                   title LIKE '%SoY%' OR title LIKE '%software%' OR
                   title LIKE '%module%' OR title LIKE '%architecture%' OR
                   title LIKE '%database%' OR title LIKE '%migration%' OR
                   title LIKE '%plugin%' OR title LIKE '%build-workflow%'
               )
               ORDER BY created_at DESC""",
            (since_date,),
        ).fetchall()
        db.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_session_handoffs(since_date: str) -> list[dict]:
    """Get session handoffs that mention SoY."""
    try:
        db = get_db()
        rows = db.execute(
            """SELECT summary, key_decisions, open_threads, created_at
               FROM session_handoffs
               WHERE created_at >= ? AND (
                   summary LIKE '%SoY%' OR summary LIKE '%software-of-you%' OR
                   summary LIKE '%module%' OR key_decisions LIKE '%SoY%' OR
                   key_decisions LIKE '%software-of-you%'
               )
               ORDER BY created_at DESC LIMIT 20""",
            (since_date,),
        ).fetchall()
        db.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_build_workflow_status() -> str:
    """Check build-workflow plugin status."""
    bw_path = PLUGIN_ROOT / "modules" / "build-workflow"
    if not bw_path.exists():
        bw_path = PLUGIN_ROOT / "skills" / "build-workflow"
    if not bw_path.exists():
        return "Not installed"

    manifest = bw_path / "manifest.json"
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text())
            return f"v{data.get('version', '?')} — {data.get('description', 'installed')}"
        except Exception:
            return "Installed (manifest unreadable)"
    return "Installed (no manifest)"


def generate_brief() -> str:
    """Generate the weekly SoY brief as markdown."""
    now = datetime.now()
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    week_start = (now - timedelta(days=now.weekday())).strftime("%B %d, %Y")

    lines = [f"# SoY Weekly Brief -- Week of {week_start}", ""]

    # Commits
    commits = get_soy_commits(week_ago)
    lines.append("## Commits This Week")
    if commits:
        for c in commits:
            lines.append(f"- `{c['short_hash']}` {c['message']} ({c['author']}, {c['date']})")
    else:
        lines.append("- No commits this week")
    lines.append("")

    # Architecture decisions
    decisions = get_recent_decisions(week_ago)
    lines.append("## Architecture Changes")
    if decisions:
        for d in decisions:
            status_tag = f" [{d['status']}]" if d.get('status') else ""
            lines.append(f"- {d['title']}{status_tag}")
            if d.get('decision'):
                lines.append(f"  Decision: {d['decision'][:200]}")
    else:
        lines.append("- No architecture decisions recorded this week")
    lines.append("")

    # Module changes
    changed_files = get_changed_files(week_ago)
    module_changes = get_module_changes(changed_files)
    new_migrations = get_new_migrations(changed_files)

    lines.append("## New/Changed Modules")
    if module_changes:
        for mod, files in sorted(module_changes.items()):
            lines.append(f"- **{mod}**: {len(files)} files changed")
    else:
        lines.append("- No module changes this week")
    if new_migrations:
        lines.append("")
        lines.append(f"New migrations: {', '.join(os.path.basename(m) for m in new_migrations)}")
    lines.append("")

    # Session handoffs mentioning SoY
    handoffs = get_session_handoffs(week_ago)
    if handoffs:
        lines.append("## Notable Sessions")
        for h in handoffs[:5]:
            summary = h.get('summary', '')[:200]
            lines.append(f"- {summary}")
            if h.get('open_threads'):
                threads = h['open_threads'][:150]
                lines.append(f"  Open threads: {threads}")
        lines.append("")

    # Needs review (anything with open_threads mentioning Kerry or build-workflow)
    lines.append("## Needs Your Review")
    review_items = []
    for h in handoffs:
        threads = h.get('open_threads', '') or ''
        if 'kerry' in threads.lower() or 'build-workflow' in threads.lower():
            review_items.append(threads[:200])
    if review_items:
        for item in review_items[:3]:
            lines.append(f"- {item}")
    else:
        lines.append("- Nothing flagged for review this week")
    lines.append("")

    # Build-workflow status
    lines.append("## Your Plugin Status")
    bw_status = get_build_workflow_status()
    lines.append(f"- build-workflow: {bw_status}")
    lines.append("")

    # Stats
    lines.append("## Stats")
    lines.append(f"- {len(commits)} commits")
    lines.append(f"- {len(changed_files)} files changed")
    lines.append(f"- {len(new_migrations)} new migrations")
    lines.append(f"- {len(module_changes)} modules touched")
    lines.append("")

    return "\n".join(lines)


def send_brief(text: str, chat_id: str = None):
    """Send the brief via Telegram."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not bot_token:
        print("ERROR: TELEGRAM_BOT_TOKEN not set", file=sys.stderr)
        return False

    if not chat_id:
        chat_id = os.environ.get("KERRY_TELEGRAM_ID", os.environ.get("TELEGRAM_OWNER_ID", ""))
    if not chat_id:
        print("ERROR: No chat ID — set KERRY_TELEGRAM_ID or TELEGRAM_OWNER_ID", file=sys.stderr)
        return False

    # Telegram has a 4096 char limit per message; split if needed
    chunks = []
    if len(text) <= 4000:
        chunks = [text]
    else:
        current = ""
        for line in text.split("\n"):
            if len(current) + len(line) + 1 > 4000:
                chunks.append(current)
                current = line + "\n"
            else:
                current += line + "\n"
        if current.strip():
            chunks.append(current)

    for chunk in chunks:
        payload = json.dumps({
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "Markdown",
        }).encode()

        req = urllib.request.Request(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
                if not result.get("ok"):
                    print(f"Telegram API error: {result}", file=sys.stderr)
                    return False
        except Exception as e:
            print(f"Failed to send Telegram message: {e}", file=sys.stderr)
            return False

    return True


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Kerry's Weekly SoY Brief")
    parser.add_argument("--send", action="store_true", help="Send via Telegram")
    parser.add_argument("--chat-id", help="Override Telegram chat ID")
    args = parser.parse_args()

    brief = generate_brief()

    if args.send:
        ok = send_brief(brief, chat_id=args.chat_id)
        if ok:
            print("Brief sent via Telegram")
        else:
            print("Failed to send brief — printing to stdout instead:")
            print(brief)
    else:
        print(brief)


if __name__ == "__main__":
    main()
