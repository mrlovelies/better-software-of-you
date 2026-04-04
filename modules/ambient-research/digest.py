#!/usr/bin/env python3
"""
Weekly Digest Generator (Tier 3)
Runs overnight via Claude CLI to synthesize findings across streams
and produce the coffee shop digest with learning workshop.

Usage:
    python3 -m modules.ambient-research.digest              # Generate weekly digest
    python3 -m modules.ambient-research.digest --preview     # Preview without saving
"""

import json
import os
import sqlite3
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path.home() / ".local" / "share" / "software-of-you" / "soy.db"
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = Path.home() / ".local" / "share" / "software-of-you" / "output"


def _find_claude_cli() -> str:
    """Find the claude CLI binary, checking nvm paths if not in PATH."""
    import shutil
    found = shutil.which("claude")
    if found:
        return found
    # Check nvm installations
    nvm_dir = Path.home() / ".nvm" / "versions" / "node"
    if nvm_dir.exists():
        for node_dir in sorted(nvm_dir.iterdir(), reverse=True):
            candidate = node_dir / "bin" / "claude"
            if candidate.exists():
                return str(candidate)
    return "claude"  # Fall back, will fail with FileNotFoundError


def get_db():
    db = sqlite3.connect(DB_PATH)
    db.text_factory = lambda b: b.decode("utf-8", errors="replace")
    db.row_factory = sqlite3.Row
    return db


def gather_digest_context() -> dict:
    """Gather all context needed for the weekly digest."""
    db = get_db()
    now = datetime.now()
    week_ago = (now - timedelta(days=7)).isoformat()

    # Active streams and their wikis
    streams = db.execute("SELECT * FROM research_streams WHERE active = 1 ORDER BY priority DESC").fetchall()
    stream_data = []
    for s in streams:
        wiki = db.execute(
            "SELECT content, updated_at FROM research_wikis WHERE stream_id = ? ORDER BY updated_at DESC LIMIT 1",
            (s["id"],),
        ).fetchone()

        findings_count = db.execute(
            "SELECT COUNT(*) as n FROM research_findings WHERE stream_id = ? AND created_at > ?",
            (s["id"], week_ago),
        ).fetchone()

        tasks = db.execute(
            "SELECT tier, task_type, status, completed_at FROM research_tasks WHERE stream_id = ? AND created_at > ? ORDER BY completed_at",
            (s["id"], week_ago),
        ).fetchall()

        stream_data.append({
            "name": s["name"],
            "description": s["description"],
            "priority": s["priority"],
            "wiki_content": wiki["content"] if wiki else "(No wiki yet)",
            "wiki_updated": wiki["updated_at"] if wiki else None,
            "findings_this_week": findings_count["n"],
            "tasks_this_week": [dict(t) for t in tasks],
        })

    # Recent project activity (from SoY)
    projects = db.execute(
        """SELECT p.name, p.description, p.status,
                  (SELECT COUNT(*) FROM tasks t WHERE t.project_id = p.id AND t.completed_at > ?) as tasks_completed,
                  (SELECT COUNT(*) FROM tasks t WHERE t.project_id = p.id AND t.status IN ('todo', 'in_progress')) as tasks_active
           FROM projects p WHERE p.status = 'active' ORDER BY p.updated_at DESC LIMIT 10""",
        (week_ago,),
    ).fetchall()

    # Recent activity log
    activity = db.execute(
        "SELECT entity_type, action, details, created_at FROM activity_log WHERE created_at > ? ORDER BY created_at DESC LIMIT 20",
        (week_ago,),
    ).fetchall()

    db.close()

    # --- Enhanced context: Signal Harvester pipeline ---
    pipeline_builds = []
    try:
        sh_db_path = Path.home() / ".software-of-you" / "data" / "soy.db"
        if sh_db_path.exists():
            # Recent pipeline experiment results
            builds_dir = Path.home() / "signal-harvester" / "builds"
            if builds_dir.exists():
                for d in sorted(builds_dir.iterdir(), reverse=True):
                    meta_path = d / ".build-meta.json"
                    if meta_path.exists():
                        try:
                            meta = json.loads(meta_path.read_text())
                            created = meta.get("created_at", "")
                            if created > week_ago:
                                files_count = sum(1 for f in d.rglob("*") if f.suffix in (".ts", ".tsx", ".js", ".jsx", ".py") and "node_modules" not in str(f) and ".gsd" not in str(f))
                                pipeline_builds.append({
                                    "name": d.name,
                                    "status": meta.get("status", "unknown"),
                                    "variant": meta.get("variant", meta.get("builder_type", "unknown")),
                                    "source_files": files_count,
                                    "created": created[:16],
                                    "product": meta.get("source_id", ""),
                                })
                        except Exception:
                            pass
    except Exception:
        pass

    # --- Enhanced context: Session handoffs ---
    session_handoffs = []
    try:
        handoffs = db.execute(
            "SELECT summary, branch, source, status, created_at FROM session_handoffs WHERE created_at > ? ORDER BY created_at DESC LIMIT 5",
            (week_ago,),
        ).fetchall()
        session_handoffs = [dict(h) for h in handoffs]
    except Exception:
        pass

    # --- Enhanced context: Memory system updates ---
    memory_updates = []
    try:
        memory_dir = Path.home() / ".claude" / "projects" / "-mnt-c-Users-jalex-wkspaces" / "memory"
        if memory_dir.exists():
            for f in sorted(memory_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
                if f.name == "MEMORY.md":
                    continue
                mtime = datetime.fromtimestamp(f.stat().st_mtime).isoformat()
                if mtime > week_ago:
                    # Read frontmatter for description
                    desc = ""
                    try:
                        text = f.read_text()[:500]
                        for line in text.split("\n"):
                            if line.startswith("description:"):
                                desc = line.split(":", 1)[1].strip()
                                break
                    except Exception:
                        pass
                    memory_updates.append({"file": f.name, "modified": mtime[:16], "description": desc})
    except Exception:
        pass

    # --- Enhanced context: Git activity across repos ---
    git_activity = []
    try:
        import subprocess
        repos = [
            Path.home() / "signal-harvester",
            Path.home() / ".software-of-you",
        ]
        for repo in repos:
            if (repo / ".git").exists():
                try:
                    result = subprocess.run(
                        ["git", "log", f"--since={week_ago[:10]}", "--oneline", "--no-merges", "-20"],
                        cwd=str(repo), capture_output=True, text=True, timeout=10
                    )
                    if result.stdout.strip():
                        for line in result.stdout.strip().split("\n")[:10]:
                            git_activity.append({"repo": repo.name, "commit": line.strip()})
                except Exception:
                    pass
    except Exception:
        pass

    # --- Enhanced context: Service states ---
    service_states = []
    try:
        import subprocess
        services = ["harvest-dashboard", "soy-hub", "soy-discord-bot", "soy-telegram-bot", "syncthing", "paperclip"]
        for svc in services:
            try:
                result = subprocess.run(
                    ["systemctl", "--user", "is-active", f"{svc}.service"],
                    capture_output=True, text=True, timeout=5
                )
                service_states.append({"name": svc, "status": result.stdout.strip()})
            except Exception:
                pass
    except Exception:
        pass

    return {
        "week_start": (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d"),
        "streams": stream_data,
        "projects": [dict(p) for p in projects],
        "activity": [dict(a) for a in activity],
        "pipeline_builds": pipeline_builds,
        "session_handoffs": session_handoffs,
        "memory_updates": memory_updates,
        "git_activity": git_activity,
        "service_states": service_states,
    }


def build_digest_prompt(context: dict) -> str:
    """Build the Claude CLI prompt for digest generation."""
    streams_text = ""
    for s in context["streams"]:
        streams_text += f"\n### {s['name']} (priority: {s['priority']})\n"
        streams_text += f"Description: {s['description']}\n"
        streams_text += f"Findings this week: {s['findings_this_week']}\n"
        streams_text += f"Wiki last updated: {s['wiki_updated'] or 'never'}\n"
        streams_text += f"\nCurrent wiki:\n{s['wiki_content'][:2000]}\n"

    projects_text = ""
    for p in context["projects"]:
        projects_text += f"- **{p['name']}**: {p['description'] or 'No description'} "
        projects_text += f"({p['tasks_completed']} tasks completed, {p['tasks_active']} active)\n"

    activity_text = "\n".join(
        f"- [{a['created_at'][:10]}] {a['entity_type']}/{a['action']}: {a['details']}"
        for a in context["activity"][:10]
    )

    # Pipeline builds
    pipeline_text = ""
    if context.get("pipeline_builds"):
        pipeline_text = "\n## Signal Harvester Pipeline Activity\n"
        for b in context["pipeline_builds"]:
            pipeline_text += f"- **{b['name']}**: {b['status']} ({b['source_files']} files, {b['variant']}) — {b['created']}\n"

    # Session handoffs
    handoffs_text = ""
    if context.get("session_handoffs"):
        handoffs_text = "\n## Claude Code Session Summaries\n"
        for h in context["session_handoffs"]:
            summary = (h.get("summary") or "")[:300]
            handoffs_text += f"- [{h.get('created_at', '?')[:10]}] {h.get('source', '?')}: {summary}\n"

    # Memory updates
    memory_text = ""
    if context.get("memory_updates"):
        memory_text = "\n## New Learnings & Context (Memory System)\n"
        for m in context["memory_updates"]:
            memory_text += f"- **{m['file']}** ({m['modified']}): {m['description']}\n"

    # Git activity
    git_text = ""
    if context.get("git_activity"):
        git_text = "\n## Git Commits This Week\n"
        for g in context["git_activity"]:
            git_text += f"- [{g['repo']}] {g['commit']}\n"

    # Service states
    service_text = ""
    if context.get("service_states"):
        service_text = "\n## Service Health\n"
        for s in context["service_states"]:
            icon = "✅" if s["status"] == "active" else "❌"
            service_text += f"- {icon} {s['name']}: {s['status']}\n"

        return f"""You are generating a weekly intelligence digest for Alex Somerville — a freelance developer,
voice actor, and game developer based in Toronto.

This digest should be engaging and readable — designed to be read with coffee at a coffee shop, not skimmed
at a desk. Connect dots across research streams, highlight what shifted, what's new, and include
cross-pollination insights where one stream's findings are relevant to another.

## This Week's Research Streams
{streams_text}

## Alex's Active Projects
{projects_text or "No active project data available yet."}

## Recent Activity
{activity_text or "No recent activity logged."}

{pipeline_text}

{handoffs_text}

{memory_text}

{git_text}

{service_text}

## Instructions

IMPORTANT: The digest MUST cover ALL activity — not just research streams. If pipeline experiments ran,
products were built, sessions happened, or memories were saved, those are the LEAD STORY, not the research
streams. The digest should reflect what actually happened this week, not just what the ambient research
module tracked.

Generate a weekly digest in markdown with these sections:

### 0. This Week's Headline
One sentence: the single most important thing that happened. If 6 products were built by an AI pipeline,
that's the headline — not "research streams continued."


### 1. The Big Picture (2-3 paragraphs)
What shifted this week across all streams? What's the connective thread? Write this like you're
telling a friend about interesting things you learned.

### 2. Stream Highlights
For each active stream, a brief (3-5 sentence) highlight of what's new and why it matters for Alex's work.
Skip streams with no new findings.

### 3. Cross-Pollination Corner
Insights from one stream that are unexpectedly relevant to another. Only include if genuinely useful —
don't force connections that aren't there.

### 4. This Week's Workshop
A hands-on tutorial exercise (30-60 minutes) tied to something Alex is actively working on.
Structure:
- **Context:** Why this matters for your current work
- **The Exercise:** Step-by-step, concrete, completable
- **The Payoff:** What you'll be able to do after that you couldn't before

Pick a skill that's one step adjacent to Alex's current work — something that would make his active
project work better. Calibrate to his level (deep React/TS expertise, newer to UE5/game dev).

### 5. Look Ahead
What should the research system focus on next week? Any priority shifts suggested by this week's findings?

Keep the tone warm, direct, and genuinely useful. No filler. No corporate-speak."""


def generate_digest(preview: bool = False) -> str:
    """Generate the weekly digest via Claude CLI."""
    context = gather_digest_context()
    prompt = build_digest_prompt(context)

    if preview:
        print("=== DIGEST PROMPT (preview mode) ===\n")
        print(prompt[:2000])
        print(f"\n... ({len(prompt)} chars total)")
        return ""

    print("Generating digest via Claude CLI...")
    print(f"  Streams: {len(context['streams'])}")
    print(f"  Projects: {len(context['projects'])}")

    try:
        claude_bin = _find_claude_cli()
        # Ensure node is in PATH (nvm isn't loaded in cron environments)
        env = dict(os.environ)
        claude_dir = str(Path(claude_bin).parent)
        env["PATH"] = claude_dir + ":" + env.get("PATH", "")
        proc = subprocess.run(
            [claude_bin, "-p", prompt],
            capture_output=True,
            text=True,
            timeout=600,
            cwd=str(PLUGIN_ROOT),
            env=env,
        )

        if proc.returncode != 0:
            print(f"  Error: {proc.stderr[:200]}")
            return ""

        digest_content = proc.stdout
    except subprocess.TimeoutExpired:
        print("  Error: Claude CLI timed out")
        return ""
    except FileNotFoundError:
        print("  Error: Claude CLI not found")
        return ""

    # Save to database
    db = get_db()
    db.execute(
        """INSERT INTO research_digests (week_start, title, content, workshop_content, streams_covered, generated_by)
           VALUES (?, ?, ?, ?, ?, 'claude-cli')""",
        (
            context["week_start"],
            f"Weekly Intelligence Digest — {context['week_start']}",
            digest_content,
            _extract_workshop(digest_content),
            json.dumps([s["name"] for s in context["streams"]]),
        ),
    )

    # Log activity
    db.execute(
        "INSERT INTO activity_log (entity_type, entity_id, action, details, created_at) VALUES ('research', 0, 'digest', ?, datetime('now'))",
        (f"Weekly digest generated for {context['week_start']}",),
    )
    db.commit()
    db.close()

    # Save as HTML page
    _render_digest_html(context["week_start"], digest_content)

    print(f"  Digest saved: {len(digest_content)} chars")
    return digest_content


def _extract_workshop(content: str) -> str:
    """Extract the workshop section from the digest."""
    markers = ["## This Week's Workshop", "### This Week's Workshop", "## 4. This Week's Workshop", "### 4. This Week's Workshop"]
    for marker in markers:
        if marker in content:
            start = content.index(marker)
            # Find next ## heading
            rest = content[start + len(marker):]
            next_heading = len(rest)
            for h in ["## ", "### 5"]:
                idx = rest.find(h)
                if idx > 0 and idx < next_heading:
                    next_heading = idx
            return rest[:next_heading].strip()
    return ""


def _render_digest_html(week_start: str, content: str):
    """Render digest as a standalone HTML page."""
    try:
        import re
        # Basic markdown to HTML (good enough for display)
        html_content = content
        # Headers
        html_content = re.sub(r'^### (.+)$', r'<h3>\1</h3>', html_content, flags=re.MULTILINE)
        html_content = re.sub(r'^## (.+)$', r'<h2>\1</h2>', html_content, flags=re.MULTILINE)
        html_content = re.sub(r'^# (.+)$', r'<h1>\1</h1>', html_content, flags=re.MULTILINE)
        # Bold
        html_content = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html_content)
        # Lists
        html_content = re.sub(r'^- (.+)$', r'<li>\1</li>', html_content, flags=re.MULTILINE)
        # Paragraphs
        html_content = re.sub(r'\n\n', r'</p><p>', html_content)
        # Code blocks
        html_content = re.sub(r'`([^`]+)`', r'<code>\1</code>', html_content)

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Weekly Digest — {week_start}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        body {{ font-family: 'Inter', sans-serif; }}
        .prose h1 {{ font-size: 1.875rem; font-weight: 700; margin-top: 2rem; margin-bottom: 1rem; color: #18181b; }}
        .prose h2 {{ font-size: 1.5rem; font-weight: 600; margin-top: 1.75rem; margin-bottom: 0.75rem; color: #27272a; border-bottom: 1px solid #e4e4e7; padding-bottom: 0.5rem; }}
        .prose h3 {{ font-size: 1.125rem; font-weight: 600; margin-top: 1.25rem; margin-bottom: 0.5rem; color: #3f3f46; }}
        .prose p {{ margin-bottom: 1rem; line-height: 1.75; color: #52525b; }}
        .prose li {{ margin-bottom: 0.25rem; color: #52525b; line-height: 1.75; }}
        .prose strong {{ color: #18181b; }}
        .prose code {{ background: #f4f4f5; padding: 0.125rem 0.375rem; border-radius: 0.25rem; font-size: 0.875rem; }}
    </style>
</head>
<body class="bg-zinc-50 min-h-screen">
    <div class="max-w-3xl mx-auto px-6 py-12">
        <div class="mb-8">
            <p class="text-sm text-zinc-400 uppercase tracking-wider">Son of Anton</p>
            <h1 class="text-3xl font-bold text-zinc-900 mt-1">Weekly Intelligence Digest</h1>
            <p class="text-zinc-500 mt-1">Week of {week_start}</p>
        </div>
        <div class="prose bg-white rounded-xl shadow-sm border border-zinc-200 p-8">
            <p>{html_content}</p>
        </div>
        <p class="text-center text-zinc-400 text-sm mt-8">Generated by Son of Anton</p>
    </div>
</body>
</html>"""

        output_path = OUTPUT_DIR / f"digest-{week_start}.html"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html)
        print(f"  HTML: {output_path}")

    except Exception as e:
        print(f"  HTML render error: {e}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Weekly Digest Generator")
    parser.add_argument("--preview", action="store_true", help="Preview prompt without generating")
    args = parser.parse_args()
    generate_digest(preview=args.preview)
