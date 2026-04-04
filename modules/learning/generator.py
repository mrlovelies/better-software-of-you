#!/usr/bin/env python3
"""
Learning Module — Prompt construction + Claude CLI invocation.
Generates daily digests and weekly workshops.
"""

import importlib.util
import json
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]

# Import sibling modules
_mod_dir = Path(__file__).resolve().parent

_context_spec = importlib.util.spec_from_file_location("context", _mod_dir / "context.py")
context_mod = importlib.util.module_from_spec(_context_spec)
_context_spec.loader.exec_module(context_mod)

_profile_spec = importlib.util.spec_from_file_location("profile", _mod_dir / "profile.py")
profile_mod = importlib.util.module_from_spec(_profile_spec)
_profile_spec.loader.exec_module(profile_mod)


def _format_context_section(label: str, items: list[dict], max_items: int = 15) -> str:
    """Format a context section for the prompt."""
    if not items:
        return f"### {label}\nNo data available.\n"

    text = f"### {label}\n"
    for item in items[:max_items]:
        # Compact representation
        parts = []
        for k, v in item.items():
            if v is not None and k not in ("source",):
                if isinstance(v, str) and len(v) > 200:
                    v = v[:200] + "..."
                parts.append(f"{k}: {v}")
        text += "- " + " | ".join(parts) + "\n"
    if len(items) > max_items:
        text += f"  ... and {len(items) - max_items} more\n"
    return text + "\n"


def _format_profile(profile: dict) -> str:
    """Format the learning profile for prompt injection."""
    if not profile:
        return "No learning profile yet — this is a first-time digest. Use medium depth (3/5) and balanced explanations."

    lines = []
    if "depth" in profile:
        for domain, level in profile["depth"].items():
            lines.append(f"- {domain}: depth level {level}/5")
    if "style" in profile:
        for key, val in profile["style"].items():
            lines.append(f"- {key}: {val}")
    if "effective_style" in profile:
        styles = sorted(profile["effective_style"].items(), key=lambda x: -int(x[1]))
        if styles:
            lines.append(f"- Most effective section types: {', '.join(f'{s[0]} ({s[1]}x)' for s in styles[:3])}")

    return "\n".join(lines) if lines else "Profile exists but no specific preferences recorded yet."


def _build_health_section() -> str:
    """Check system health and data freshness for the digest header."""
    import sqlite3
    db = sqlite3.connect(str(context_mod.DB_PATH), timeout=30)
    db.row_factory = sqlite3.Row
    issues = []

    try:
        # Check data freshness
        meta = {r["key"]: r["value"] for r in db.execute(
            "SELECT key, value FROM soy_meta WHERE key IN ('gmail_last_synced', 'calendar_last_synced', 'transcripts_last_scanned')"
        ).fetchall()}

        for key, label in [("gmail_last_synced", "Gmail"), ("calendar_last_synced", "Calendar")]:
            ts = meta.get(key)
            if not ts:
                issues.append(f"- **{label}**: Never synced")
            else:
                try:
                    last = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
                    days_ago = (datetime.now() - last).days
                    if days_ago > 1:
                        issues.append(f"- **{label}**: Last synced {days_ago} days ago ({ts[:10]})")
                except ValueError:
                    issues.append(f"- **{label}**: Invalid sync timestamp")

        # Check for recent errors in events table
        try:
            error_count = db.execute(
                "SELECT COUNT(*) as c FROM events WHERE event_type = 'error' AND created_at > datetime('now', '-24 hours')"
            ).fetchone()
            if error_count and error_count["c"] > 0:
                issues.append(f"- **Errors**: {error_count['c']} errors in the last 24 hours")
        except Exception:
            pass

        # Check agent health via events
        try:
            fails = db.execute(
                "SELECT source, COUNT(*) as c FROM events WHERE event_type = 'agent_failed' "
                "AND created_at > datetime('now', '-24 hours') GROUP BY source"
            ).fetchall()
            for f in fails:
                issues.append(f"- **{f['source']}**: {f['c']} failures in last 24h")
        except Exception:
            pass

    except Exception:
        issues.append("- **Health check failed**: Could not query system status")
    finally:
        db.close()

    if not issues:
        return "### System Health\nAll data sources are fresh and no errors detected.\n\n"
    return "### System Health — ISSUES DETECTED\n" + "\n".join(issues) + "\n\n"


def build_daily_prompt(ctx: dict, profile: dict) -> str:
    """Build the prompt for a daily educational digest."""
    profile_text = _format_profile(profile)

    context_sections = ""
    context_sections += _format_context_section("Session Handoffs", ctx.get("handoffs", []))
    context_sections += _format_context_section("Claude Code Sessions", ctx.get("claude_sessions", []))
    context_sections += _format_context_section("Git Commits", ctx.get("git", []))
    context_sections += _format_context_section("Emails", ctx.get("emails", []))
    context_sections += _format_context_section("Calendar Events", ctx.get("calendar", []))
    context_sections += _format_context_section("Conversations & Transcripts", ctx.get("conversations", []))
    context_sections += _format_context_section("Research Findings", ctx.get("research", []))
    context_sections += _format_context_section("Platform Health", ctx.get("health", []))
    context_sections += _format_context_section("Project Activity", ctx.get("project_activity", []))
    context_sections += _format_context_section("Bot Conversations (Telegram & Discord)", ctx.get("bot_conversations", []))

    health_section = _build_health_section()
    today = datetime.now().strftime("%A, %B %d, %Y")

    return f"""You are generating a daily educational digest for Alex Somerville — a freelance developer,
voice actor, and indie game dev based in Toronto. Alex is experienced with React, TypeScript, and Python,
and is building a personal data platform called Software of You.

This is the morning digest for {today}. Its purpose is EDUCATIONAL — explain the WHY behind yesterday's
decisions, patterns used, tradeoffs made, and concepts encountered. Don't just summarize what happened —
teach something from it.

## Learning Profile (calibrate your depth and style to this)
{profile_text}

## System Health (MUST include in first section if there are issues)
{health_section}

## Yesterday's Raw Data
{context_sections}

## Output Format

Return a JSON array of sections. Each section has:
- "id": unique identifier like "daily-YYYY-MM-DD-sec-N"
- "type": one of "recap", "concept", "pattern", "exercise", "health", "status"
- "title": short, engaging title
- "content": markdown content (2-4 paragraphs). Explain WHY, not just what.
- "domain": the technical domain (e.g., "react", "python", "infrastructure", "git", "architecture")
- "depth_level": 1-5 matching the profile depth for this domain

Guidelines:
- 4-6 sections per digest
- **FIRST section MUST be type "status"** — system health and action items. If the System Health section above shows issues, lead with those prominently. If all clear, a brief "all systems healthy" line followed by the day's main theme.
- Include at least one "concept" or "pattern" section that teaches something
- If there's enough material, include an "exercise" — a small challenge related to yesterday's work
- Tone: warm, direct, conversational. Like a mentor explaining things over coffee.
- Reference specific commits, emails, or events when explaining concepts.
- Depth should match the learning profile — don't over-explain things Alex already knows.
- Do NOT include any preamble, thinking, or commentary outside the JSON array.

Return ONLY the JSON array, no other text."""


def build_weekly_prompt(ctx: dict, profile: dict) -> str:
    """Build the prompt for a weekly workshop."""
    profile_text = _format_profile(profile)

    context_sections = ""
    context_sections += _format_context_section("Session Handoffs", ctx.get("handoffs", []))
    context_sections += _format_context_section("Claude Code Sessions", ctx.get("claude_sessions", []))
    context_sections += _format_context_section("Git Commits This Week", ctx.get("git", []))
    context_sections += _format_context_section("Research Findings", ctx.get("research", []))
    context_sections += _format_context_section("Project Activity", ctx.get("project_activity", []))
    context_sections += _format_context_section("Conversations", ctx.get("conversations", []))
    context_sections += _format_context_section("Platform Health", ctx.get("health", []))
    context_sections += _format_context_section("Bot Conversations (Telegram & Discord)", ctx.get("bot_conversations", []))

    today = datetime.now().strftime("%A, %B %d, %Y")

    return f"""You are generating a weekly hands-on workshop for Alex Somerville — a freelance developer,
voice actor, and indie game dev based in Toronto.

This is the Sunday workshop for the week ending {today}. It should be a focused, practical tutorial
that builds on the week's actual work and research.

## Learning Profile
{profile_text}

## This Week's Activity
{context_sections}

## Output Format

Return a JSON array of sections. Each section has:
- "id": unique identifier like "weekly-YYYY-MM-DD-sec-N"
- "type": one of "overview", "concept", "tutorial", "exercise", "reflection"
- "title": short, engaging title
- "content": markdown content. Tutorial sections should have step-by-step instructions.
- "domain": the technical domain
- "depth_level": 1-5 matching the profile

Guidelines:
- 5-8 sections total
- Start with an "overview" connecting the week's themes
- The core should be a "tutorial" — a 30-60 minute hands-on exercise tied to real work
- Structure the tutorial with: Context (why this matters), Steps (concrete, completable), Payoff (what you can do after)
- Include a "reflection" section at the end connecting the tutorial to Alex's broader goals
- Pick a skill one step adjacent to current work — something that makes active projects better
- Calibrate to the profile: deep React/TS expertise, growing Python skills, newer to infra/DevOps
- Tone: mentor-like, practical, no fluff

Return ONLY the JSON array, no other text."""


def generate(digest_type: str) -> dict | None:
    """Orchestrate: gather context -> build prompt -> claude -p -> parse -> store -> notify."""
    import sqlite3
    import sys
    sys.path.insert(0, str(PLUGIN_ROOT / "shared"))
    try:
        from agent_heartbeat import agent_start, agent_complete, agent_fail
    except ImportError:
        agent_start = agent_complete = agent_fail = lambda *a, **k: "no-heartbeat"

    run_id = agent_start("learning", f"Generating {digest_type} digest")

    db = sqlite3.connect(context_mod.DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row

    # Determine time range
    now = datetime.now()
    if digest_type == "daily":
        since = (now - timedelta(days=1)).strftime("%Y-%m-%d 00:00:00")
        digest_date = now.strftime("%Y-%m-%d")
    else:  # weekly
        since = (now - timedelta(days=7)).strftime("%Y-%m-%d 00:00:00")
        digest_date = now.strftime("%Y-%m-%d")

    # Check if already generated
    existing = db.execute(
        "SELECT id FROM learning_digests WHERE digest_type = ? AND digest_date = ?",
        (digest_type, digest_date),
    ).fetchone()
    if existing:
        print(f"Digest already generated for {digest_type} {digest_date}")
        db.close()
        return None

    # Gather context
    print(f"Gathering context since {since}...")
    ctx = context_mod.gather_all(since)
    profile = ctx.pop("profile", {})

    # Check if there's enough data
    total_items = sum(len(v) for v in ctx.values() if isinstance(v, list))
    if total_items < 3:
        print(f"Not enough data ({total_items} items). Skipping generation.")
        db.close()
        return None

    # Build prompt
    if digest_type == "daily":
        prompt = build_daily_prompt(ctx, profile)
    else:
        prompt = build_weekly_prompt(ctx, profile)

    # Call Claude CLI
    print(f"Generating {digest_type} digest via Claude CLI...")
    start_time = time.time()

    try:
        proc = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "text"],
            capture_output=True,
            text=True,
            timeout=600,
            cwd=str(PLUGIN_ROOT),
        )
        if proc.returncode != 0:
            print(f"Error: {proc.stderr[:300]}")
            db.close()
            return None

        raw_output = proc.stdout.strip()
    except subprocess.TimeoutExpired:
        print("Error: Claude CLI timed out")
        db.close()
        return None
    except FileNotFoundError:
        print("Error: Claude CLI not found")
        db.close()
        return None

    duration_ms = int((time.time() - start_time) * 1000)

    # Parse JSON output
    try:
        # Strip potential markdown code fences
        if raw_output.startswith("```"):
            raw_output = raw_output.split("\n", 1)[1]
        if raw_output.endswith("```"):
            raw_output = raw_output.rsplit("```", 1)[0]
        raw_output = raw_output.strip()

        sections = json.loads(raw_output)
        if not isinstance(sections, list):
            print(f"Error: Expected JSON array, got {type(sections)}")
            db.close()
            return None
    except json.JSONDecodeError as e:
        print(f"Error parsing JSON: {e}")
        print(f"Raw output (first 500 chars): {raw_output[:500]}")
        db.close()
        return None

    # Validate sections
    for s in sections:
        if not all(k in s for k in ("id", "type", "title", "content")):
            print(f"Warning: Section missing required fields: {s.get('id', 'unknown')}")

    # Generate title
    if digest_type == "daily":
        title = f"Daily Digest — {now.strftime('%A, %B %d')}"
    else:
        title = f"Weekly Workshop — Week of {(now - timedelta(days=now.weekday())).strftime('%B %d')}"

    # Store to DB
    sources = {k: len(v) for k, v in ctx.items() if isinstance(v, list) and v}

    db.execute(
        """INSERT INTO learning_digests
           (digest_type, digest_date, title, sections, sources, model, tokens_used, generation_duration_ms, created_at)
           VALUES (?, ?, ?, ?, ?, 'claude-cli', NULL, ?, datetime('now'))""",
        (digest_type, digest_date, title, json.dumps(sections), json.dumps(sources), duration_ms),
    )

    # Log activity
    db.execute(
        """INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
           VALUES ('learning', 0, 'digest_generated', ?, datetime('now'))""",
        (json.dumps({"type": digest_type, "date": digest_date, "sections": len(sections)}),),
    )

    db.commit()

    digest_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.close()

    print(f"Digest saved: {len(sections)} sections, {duration_ms}ms")

    agent_complete("learning", run_id, f"{digest_type} digest: {len(sections)} sections",
                   {"sections": len(sections), "duration_ms": duration_ms})

    # Send Telegram notification
    _notify_telegram(digest_type, title, len(sections))

    return {
        "id": digest_id,
        "type": digest_type,
        "date": digest_date,
        "title": title,
        "sections": len(sections),
        "duration_ms": duration_ms,
    }


HUB_URL = "https://soy.tail2272ce.ts.net"


def _notify_telegram(digest_type: str, title: str, section_count: int):
    """Send Telegram notification with inline button linking to the hub."""
    import os
    import urllib.request

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    owner_id = os.environ.get("TELEGRAM_OWNER_ID", "")
    if not bot_token or not owner_id:
        return

    emoji = "📖" if digest_type == "daily" else "🔬"
    msg = f"{emoji} *New {digest_type.title()} Digest*\n\n"
    msg += f"{title}\n"
    msg += f"{section_count} sections ready to read"

    keyboard = {
        "inline_keyboard": [[
            {"text": f"Open {digest_type.title()} Digest", "url": f"{HUB_URL}/learning"}
        ]]
    }

    try:
        payload = json.dumps({
            "chat_id": owner_id,
            "text": msg,
            "parse_mode": "Markdown",
            "reply_markup": keyboard,
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass
