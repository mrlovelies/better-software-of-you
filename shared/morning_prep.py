#!/usr/bin/env python3
"""
Morning Prep Autopilot — Generates meeting prep briefs for today's calendar.

Runs at 6am. Queries today's calendar events, pulls contact context for
attendees, and writes prep briefs to the dashboard output directory.

Usage:
    python3 shared/morning_prep.py
"""

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(PLUGIN_ROOT, "data", "soy.db")
DATA_DIR = Path.home() / ".local" / "share" / "software-of-you"
OUTPUT_DIR = DATA_DIR / "output"
LOG_FILE = DATA_DIR / "morning-prep.log"


def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def send_telegram(msg):
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
    payload = json.dumps({"chat_id": owner, "text": msg, "parse_mode": "Markdown"}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload, headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


def get_todays_events(db):
    """Get calendar events for today."""
    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    events = db.execute("""
        SELECT id, title, start_time, end_time, location, description, attendees
        FROM calendar_events
        WHERE date(start_time) = date(?)
        ORDER BY start_time
    """, (today,)).fetchall()

    return events


def get_attendee_context(db, attendees_json):
    """Look up contacts for event attendees."""
    if not attendees_json:
        return []

    try:
        attendees = json.loads(attendees_json)
    except (json.JSONDecodeError, TypeError):
        return []

    context = []
    for att in attendees:
        email = att.get("email", "") if isinstance(att, dict) else str(att)
        if not email:
            continue

        contact = db.execute("""
            SELECT c.id, c.name, c.company, c.role,
                   (SELECT content FROM notes WHERE entity_type='contact' AND entity_id=c.id ORDER BY created_at DESC LIMIT 1) as last_note,
                   (SELECT action || ': ' || details FROM activity_log WHERE entity_type='contact' AND entity_id=c.id ORDER BY created_at DESC LIMIT 1) as last_activity
            FROM contacts c
            WHERE c.email = ? OR c.email LIKE ?
            LIMIT 1
        """, (email, f"%{email}%")).fetchone()

        if contact:
            context.append({
                "name": contact["name"],
                "company": contact["company"],
                "role": contact["role"],
                "last_note": contact["last_note"],
                "last_activity": contact["last_activity"],
                "email": email,
            })
        else:
            context.append({"name": email, "email": email})

    return context


def generate_prep_brief(event, attendee_context):
    """Generate a markdown prep brief for an event."""
    start = event["start_time"] or "?"
    end = event["end_time"] or "?"

    # Parse time for display
    try:
        start_dt = datetime.fromisoformat(start)
        time_str = start_dt.strftime("%-I:%M %p")
    except (ValueError, TypeError):
        time_str = start

    brief = f"## {event['title']}\n**Time:** {time_str}\n"

    if event["location"]:
        brief += f"**Location:** {event['location']}\n"

    brief += "\n### Attendees\n"
    for att in attendee_context:
        name = att.get("name", "Unknown")
        company = att.get("company")
        role = att.get("role")

        line = f"- **{name}**"
        if company or role:
            parts = [p for p in [role, company] if p]
            line += f" — {', '.join(parts)}"
        brief += line + "\n"

        if att.get("last_note"):
            brief += f"  - Last note: {att['last_note'][:150]}\n"
        if att.get("last_activity"):
            brief += f"  - Recent: {att['last_activity'][:150]}\n"

    if event["description"]:
        brief += f"\n### Event Notes\n{event['description'][:500]}\n"

    return brief


def run():
    log("=== Morning Prep Autopilot ===")
    db = get_db()

    events = get_todays_events(db)
    if not events:
        log("No events today — nothing to prep")
        db.close()
        return

    log(f"Found {len(events)} events today")

    briefs = []
    for event in events:
        attendee_ctx = get_attendee_context(db, event["attendees"])
        brief = generate_prep_brief(event, attendee_ctx)
        briefs.append(brief)
        log(f"  Prepped: {event['title']}")

    db.close()

    if not briefs:
        return

    # Write combined brief
    today_str = datetime.now().strftime("%Y-%m-%d")
    header = f"# Morning Prep — {datetime.now().strftime('%A, %B %-d')}\n\n"
    full_brief = header + "\n---\n\n".join(briefs)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / f"morning-prep-{today_str}.md"
    output_path.write_text(full_brief)
    log(f"Written to {output_path}")

    # Send summary to Telegram
    event_list = "\n".join(f"  • {e['title']}" for e in events)
    send_telegram(f"☀️ *Morning Prep Ready*\n\n{len(events)} meetings today:\n{event_list}\n\nPrep briefs generated.")

    log("=== Morning Prep Complete ===")


if __name__ == "__main__":
    run()
