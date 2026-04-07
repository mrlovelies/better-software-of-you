#!/usr/bin/env python3
"""
QPack Demo — Shows the product experience, not the pipeline internals.

    python3 modules/qpack-generator/demo.py

This simulates what a user would see in the SoY Desktop GUI:
home screen suggestions, clicking a question, getting a formatted answer.
"""

import json
import sqlite3
import sys
import io
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

DB_PATH = Path.home() / ".local" / "share" / "software-of-you" / "soy.db"
QPACK_DIR = Path(__file__).resolve().parents[2] / "qpacks"

# ANSI colors for terminal rendering
DIM = "\033[2m"
BOLD = "\033[1m"
RED = "\033[31m"
AMBER = "\033[33m"
BLUE = "\033[34m"
GREEN = "\033[32m"
CYAN = "\033[36m"
ZINC = "\033[37m"
RESET = "\033[0m"

COLOR_MAP = {"red": RED, "amber": AMBER, "blue": BLUE, "green": GREEN, "zinc": ZINC, "cyan": CYAN}


def _get_user_name():
    try:
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT value FROM user_profile WHERE category='identity' AND key='name'").fetchone()
        db.close()
        return row["value"] if row else "there"
    except Exception:
        return "there"


def _progress_bar(pct, width=20):
    filled = int(width * pct / 100)
    return "█" * filled + "░" * (width - filled)


def _relative_time(iso_str):
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        delta = datetime.now() - dt.replace(tzinfo=None)
        days = delta.days
        if days == 0:
            return "today"
        elif days == 1:
            return "yesterday"
        elif days < 7:
            return f"{days} days ago"
        elif days < 30:
            return f"{days // 7} weeks ago"
        else:
            return f"{days // 30} months ago"
    except Exception:
        return str(iso_str)[:10]


def run_demo():
    # --- Silently generate QPacks ---
    from pipeline import Pipeline
    from steps import ScanStep, TemplateStep, FilterStep, ValidateStep, AdaptStep, DeployStep

    buf = io.StringIO()
    with redirect_stdout(buf):
        p = Pipeline([ScanStep(), TemplateStep(), FilterStep(), ValidateStep(), AdaptStep(), DeployStep()])
        ctx = p.run()

    data_state = ctx.get("data_state", {})
    deployed = ctx.get("deployed", [])
    total_q = sum(d["questions"] for d in deployed)
    name = _get_user_name()

    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    # ================================================================
    # HOME SCREEN
    # ================================================================
    print()
    print(f"  {BOLD}Good {'morning' if datetime.now().hour < 12 else 'afternoon' if datetime.now().hour < 17 else 'evening'}, {name}.{RESET}")
    print()

    # Smart suggestions
    try:
        from suggestions import get_smart_suggestions
        suggestions = get_smart_suggestions()
    except Exception:
        suggestions = []

    if suggestions:
        for s in suggestions[:3]:
            color = COLOR_MAP.get(s.get("color", "zinc"), ZINC)
            icon = {"amber": "!", "red": "!!", "blue": "i", "green": "+", "zinc": "."}
            icon_char = icon.get(s.get("color", "zinc"), ".")
            print(f"  {color}[{icon_char}]{RESET} {s['label']}")
    print()

    # Quick stats line
    contacts = data_state.get("contacts", 0)
    emails = data_state.get("emails", 0)
    projects = data_state.get("projects", 0)
    tasks = data_state.get("tasks", 0)
    print(f"  {DIM}{contacts} contacts  {emails} emails  {projects} projects  {tasks} tasks{RESET}")
    print(f"  {DIM}{total_q} questions available across {len(deployed)} modules{RESET}")

    # ================================================================
    # QUESTION: Which relationships are going cold?
    # ================================================================
    print(f"\n  {'─' * 56}")
    print(f"\n  {BOLD}> Which relationships are going cold?{RESET}")
    print()

    rows = db.execute("""
        SELECT name, company, days_silent, emails_30d, interactions_30d,
               relationship_depth, trajectory
        FROM v_contact_health
        WHERE days_silent > 21
        ORDER BY days_silent DESC
    """).fetchall()

    if rows:
        for row in rows[:8]:
            name_str = row["name"]
            company = f" ({row['company']})" if row["company"] else ""
            days = row["days_silent"]
            emails = row["emails_30d"] or 0
            trajectory = row["trajectory"] or "—"

            if days > 60:
                color = RED
            elif days > 30:
                color = AMBER
            else:
                color = ZINC

            print(f"  {color}{name_str}{company}{RESET}")
            print(f"    {days} days silent  |  {emails} emails this month  |  trajectory: {trajectory}")
            print()
    else:
        print(f"  {GREEN}All relationships are active. Nice.{RESET}")
        print()

    # ================================================================
    # QUESTION: How are my projects tracking?
    # ================================================================
    print(f"  {'─' * 56}")
    print(f"\n  {BOLD}> How are my projects tracking?{RESET}")
    print()

    rows = db.execute("""
        SELECT name, status, completion_pct, total_tasks, done_tasks,
               overdue_tasks, days_to_target, client_name, days_since_activity
        FROM v_project_health
        ORDER BY CASE WHEN overdue_tasks > 0 THEN 0 ELSE 1 END,
                 completion_pct DESC
    """).fetchall()

    for row in rows[:10]:
        pct = row["completion_pct"] or 0
        name_str = row["name"]
        client = f"  {DIM}({row['client_name']}){RESET}" if row["client_name"] else ""
        overdue = row["overdue_tasks"] or 0
        total = row["total_tasks"] or 0
        done = row["done_tasks"] or 0
        days_target = row["days_to_target"]
        days_active = row["days_since_activity"] or 0

        bar = _progress_bar(pct)

        # Color based on health
        if overdue > 0:
            color = RED
            flag = f"  {RED}{overdue} overdue{RESET}"
        elif days_active > 14:
            color = AMBER
            flag = f"  {AMBER}stale ({days_active}d){RESET}"
        elif pct >= 70:
            color = GREEN
            flag = ""
        else:
            color = RESET
            flag = ""

        target_str = f"  due in {days_target}d" if days_target and days_target > 0 else ""

        print(f"  {color}{name_str}{RESET}{client}")
        print(f"    {bar}  {pct}%  ({done}/{total} tasks){flag}{target_str}")

    print()

    # ================================================================
    # QUESTION: What needs my attention?
    # ================================================================
    print(f"  {'─' * 56}")
    print(f"\n  {BOLD}> What needs my attention?{RESET}")
    print()

    nudges = db.execute("""
        SELECT nudge_type, tier, entity_name, description, days_value, extra_context
        FROM v_nudge_items
        ORDER BY CASE tier WHEN 'urgent' THEN 0 WHEN 'soon' THEN 1 ELSE 2 END,
                 days_value DESC
    """).fetchall()

    if nudges:
        current_tier = None
        for nudge in nudges[:12]:
            tier = nudge["tier"]
            if tier != current_tier:
                tier_color = {"urgent": RED, "soon": AMBER, "awareness": BLUE}.get(tier, ZINC)
                tier_label = {"urgent": "URGENT", "soon": "COMING UP", "awareness": "AWARENESS"}.get(tier, tier)
                print(f"  {tier_color}{BOLD}{tier_label}{RESET}")
                current_tier = tier

            ntype = nudge["nudge_type"]
            entity = nudge["entity_name"] or "—"
            desc = nudge["description"] or ""
            days = nudge["days_value"] or 0
            extra = nudge["extra_context"] or ""

            type_icon = {
                "follow_up": "clock", "commitment": "target", "task": "check",
                "cold_contact": "user", "stale_project": "folder",
                "decision": "branch", "untracked_contact": "user-plus",
            }.get(ntype, "dot")

            context = f"  {DIM}{extra}{RESET}" if extra else ""
            days_str = f"{days}d" if days else ""

            print(f"    {entity}  {DIM}{desc}{RESET}")
            if days_str:
                print(f"    {DIM}{days_str} — {ntype}{RESET}{context}")
            print()
    else:
        print(f"  {GREEN}Nothing urgent. You're clear.{RESET}")
        print()

    # ================================================================
    # QUESTION: What emails need my reply?
    # ================================================================
    print(f"  {'─' * 56}")
    print(f"\n  {BOLD}> What emails need my reply?{RESET}")
    print()

    emails = db.execute("""
        SELECT contact_name, from_name, subject, days_old, urgency
        FROM v_email_response_queue
        WHERE contact_id IS NOT NULL
        ORDER BY CASE urgency WHEN 'overdue' THEN 0 WHEN 'aging' THEN 1 ELSE 2 END,
                 days_old DESC
        LIMIT 8
    """).fetchall()

    if emails:
        for e in emails:
            name_str = e["contact_name"] or e["from_name"] or "Unknown"
            subject = (e["subject"] or "(no subject)")[:50]
            days = e["days_old"] or 0
            urgency = e["urgency"]

            color = RED if urgency == "overdue" else AMBER if urgency == "aging" else ZINC
            print(f"  {color}{name_str}{RESET}  {DIM}{subject}{RESET}")
            print(f"    {days} days old  |  {urgency}")
            print()

        # Count total
        total = db.execute("SELECT COUNT(*) as n FROM v_email_response_queue").fetchone()["n"]
        shown = len(emails)
        if total > shown:
            print(f"  {DIM}... and {total - shown} more{RESET}")
            print()
    else:
        print(f"  {GREEN}Inbox zero. All caught up.{RESET}")
        print()

    # ================================================================
    # ROUTER: Natural language queries
    # ================================================================
    print(f"  {'─' * 56}")
    print(f"\n  {BOLD}> Search: \"Jessica\"{RESET}")
    print()

    try:
        from router import route_query
        result = route_query("Jessica")
        entity = result.get("entity_match")
        if entity:
            # Pull contact health
            ch = db.execute(
                "SELECT * FROM v_contact_health WHERE id = ?", (entity["id"],)
            ).fetchone()
            if ch:
                print(f"  {BOLD}{ch['name']}{RESET}  {DIM}{ch['company'] or ''}{RESET}")
                print()
                depth = ch["relationship_depth"] or "—"
                traj = ch["trajectory"] or "—"
                silent = ch["days_silent"] or 0
                emails_30 = ch["emails_30d"] or 0
                your_commits = ch["your_open_commitments"] or 0
                their_commits = ch["their_open_commitments"] or 0
                projects = ch["active_projects"] or 0

                traj_color = GREEN if traj == "improving" else RED if traj == "declining" else ZINC
                silent_color = RED if silent > 30 else AMBER if silent > 14 else GREEN

                print(f"    Relationship depth   {depth}/10")
                print(f"    Trajectory           {traj_color}{traj}{RESET}")
                print(f"    Last contact         {silent_color}{silent} days ago{RESET}")
                print(f"    Emails (30d)         {emails_30}")
                print(f"    Active projects      {projects}")
                if your_commits or their_commits:
                    print(f"    You owe them         {your_commits} commitments")
                    print(f"    They owe you         {their_commits} commitments")
                print()

                # Contextual questions
                print(f"  {DIM}Quick questions:{RESET}")
                print(f"    How is my relationship with {ch['name']}?")
                print(f"    What do I owe {ch['name']}?")
                print(f"    What's {ch['name']} been emailing about?")
                print()
    except Exception as e:
        print(f"  {DIM}(router: {e}){RESET}")

    # ================================================================
    # EXTENSION PREVIEW: Speed-to-Lead
    # ================================================================
    print(f"  {'─' * 56}")
    print(f"\n  {BOLD}> Speed-to-Lead{RESET}  {DIM}(extension — not installed){RESET}")
    print()

    stl_template = Path(__file__).resolve().parent / "templates" / "speed-to-lead.json"
    if stl_template.exists():
        stl = json.loads(stl_template.read_text())
        persona = stl.get("persona", {})
        questions = stl.get("questions", [])

        print(f"  {DIM}Persona: {persona.get('name', '?')} — {persona.get('tone', '?')}{RESET}")
        print(f"  {DIM}When the speed-to-lead extension is installed, these questions activate:{RESET}")
        print()
        for q in questions:
            featured = f"  {CYAN}*{RESET}" if q.get("featured") else ""
            llm = f"  {DIM}[LLM]{RESET}" if q.get("requires_llm") else ""
            print(f"    {q['label']}{featured}{llm}")
        print()
        print(f"  {DIM}The pipeline detected this template but filtered all 5 questions —{RESET}")
        print(f"  {DIM}the stl_leads table doesn't exist yet. Install the extension,{RESET}")
        print(f"  {DIM}run the migration, and they light up automatically.{RESET}")
    print()

    # ================================================================
    # FOOTER
    # ================================================================
    print(f"  {'─' * 56}")
    print()
    print(f"  {DIM}This is what the SoY Desktop GUI would render.{RESET}")
    print(f"  {DIM}Every answer above came from a QPack question —{RESET}")
    print(f"  {DIM}pre-built SQL against computed views, no LLM needed.{RESET}")
    print(f"  {DIM}The pipeline generated {total_q} questions across {len(deployed)} modules in <1s.{RESET}")
    print()
    print(f"  {DIM}For the engine internals: python3 modules/qpack-generator/run.py scan{RESET}")
    print(f"  {DIM}For the HTTP API:         python3 modules/qpack-generator/serve.py{RESET}")
    print()

    db.close()


if __name__ == "__main__":
    run_demo()
