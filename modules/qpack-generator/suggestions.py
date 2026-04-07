"""
Smart Suggestions Engine — computes the most relevant QPack questions
to surface on the home screen based on actual data state.

The priority algorithm checks conditions in order and returns the
first N that apply, falling back to featured questions from QPack files.

Usage:
    python3 modules/qpack-generator/suggestions.py
    python3 modules/qpack-generator/suggestions.py --json
    python3 modules/qpack-generator/suggestions.py --max 5
"""

import json
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path.home() / ".local" / "share" / "software-of-you" / "soy.db"
QPACK_DIR = Path(__file__).resolve().parents[2] / "qpacks"


# ──────────────────────────────────────────────────────────────────────
#  Suggestion builders — each returns a suggestion dict or None
# ──────────────────────────────────────────────────────────────────────

def _check_urgent_nudges(db: sqlite3.Connection) -> dict | None:
    """Priority 1: Urgent nudge items exist."""
    try:
        row = db.execute(
            "SELECT COUNT(*) as n FROM v_nudge_items WHERE tier = 'urgent'"
        ).fetchone()
        count = row["n"]
        if count > 0:
            return {
                "qpack_id": "nudges.attention_now",
                "label": f"{count} thing{'s' if count != 1 else ''} need{'s' if count == 1 else ''} your attention",
                "icon": "alert-triangle",
                "color": "red",
                "priority": 1,
                "data_preview": {"count": count},
            }
    except sqlite3.OperationalError:
        pass
    return None


def _check_imminent_meeting(db: sqlite3.Connection) -> dict | None:
    """Priority 2: Meeting within the next 2 hours."""
    try:
        row = db.execute(
            "SELECT title, minutes_until FROM v_meeting_prep "
            "WHERE minutes_until > 0 AND minutes_until < 120 "
            "ORDER BY minutes_until ASC LIMIT 1"
        ).fetchone()
        if row:
            title = row["title"]
            minutes = row["minutes_until"]
            if minutes < 60:
                time_str = f"in {minutes}min"
            else:
                hours = minutes // 60
                mins = minutes % 60
                time_str = f"in {hours}h{mins}m" if mins else f"in {hours}h"
            return {
                "qpack_id": "calendar.prep_next",
                "label": f"Prep for \"{title}\" {time_str}",
                "icon": "calendar-clock",
                "color": "amber",
                "priority": 2,
                "data_preview": {"title": title, "minutes_until": minutes},
            }
    except sqlite3.OperationalError:
        pass
    return None


def _check_email_backlog(db: sqlite3.Connection) -> dict | None:
    """Priority 3: More than 2 overdue/aging emails."""
    try:
        row = db.execute(
            "SELECT COUNT(*) as n FROM v_email_response_queue "
            "WHERE urgency IN ('overdue', 'aging')"
        ).fetchone()
        count = row["n"]
        if count > 2:
            return {
                "qpack_id": "email.needs_reply",
                "label": f"{count} emails need a reply",
                "icon": "mail-warning",
                "color": "amber",
                "priority": 3,
                "data_preview": {"count": count},
            }
    except sqlite3.OperationalError:
        pass
    return None


def _check_cold_active_contacts(db: sqlite3.Connection) -> dict | None:
    """Priority 4: Contacts with active projects going silent (21+ days)."""
    try:
        rows = db.execute(
            "SELECT name FROM v_contact_health "
            "WHERE days_silent > 21 AND active_projects > 0 "
            "ORDER BY days_silent DESC LIMIT 3"
        ).fetchall()
        if rows:
            names = [r["name"] for r in rows]
            count = len(names)
            if count == 1:
                label = f"{names[0]} has gone quiet — active project"
            else:
                label = f"{count} contacts with active projects going cold"
            return {
                "qpack_id": "crm.cold_relationships",
                "label": label,
                "icon": "user-x",
                "color": "amber",
                "priority": 4,
                "data_preview": {"count": count, "names": names},
            }
    except sqlite3.OperationalError:
        pass
    return None


def _check_overdue_tasks(db: sqlite3.Connection) -> dict | None:
    """Priority 5: Urgent overdue tasks."""
    try:
        row = db.execute(
            "SELECT COUNT(*) as n FROM v_nudge_items "
            "WHERE nudge_type = 'task' AND tier = 'urgent'"
        ).fetchone()
        count = row["n"]
        if count > 0:
            return {
                "qpack_id": "projects.whats_overdue",
                "label": f"{count} overdue task{'s' if count != 1 else ''}",
                "icon": "clock-alert",
                "color": "red",
                "priority": 5,
                "data_preview": {"count": count},
            }
    except sqlite3.OperationalError:
        pass
    return None


def _check_stalled_projects(db: sqlite3.Connection) -> dict | None:
    """Priority 6: Projects with no activity in 14+ days."""
    try:
        rows = db.execute(
            "SELECT name FROM v_project_health "
            "WHERE days_since_activity > 14 "
            "ORDER BY days_since_activity DESC LIMIT 3"
        ).fetchall()
        if rows:
            count = len(rows)
            names = [r["name"] for r in rows]
            if count == 1:
                label = f"\"{names[0]}\" has stalled"
            else:
                label = f"{count} projects have stalled"
            return {
                "qpack_id": "projects.stalled",
                "label": label,
                "icon": "pause-circle",
                "color": "zinc",
                "priority": 6,
                "data_preview": {"count": count, "names": names},
            }
    except sqlite3.OperationalError:
        pass
    return None


def _check_discovery_candidates(db: sqlite3.Connection) -> dict | None:
    """Priority 7: Frequent emailers not in CRM."""
    try:
        row = db.execute(
            "SELECT COUNT(*) as n FROM v_discovery_candidates"
        ).fetchone()
        count = row["n"]
        if count > 0:
            return {
                "qpack_id": "email.untracked",
                "label": f"{count} untracked emailer{'s' if count != 1 else ''} found",
                "icon": "user-search",
                "color": "blue",
                "priority": 7,
                "data_preview": {"count": count},
            }
    except sqlite3.OperationalError:
        pass
    return None


def _get_featured_fallbacks(qpack_dir: Path = QPACK_DIR) -> list[dict]:
    """Fallback: featured questions from QPack files."""
    fallbacks = []
    if not qpack_dir.exists():
        return fallbacks

    # Icon and color mapping for fallback featured questions
    MODULE_STYLE = {
        "crm": {"icon": "users", "color": "blue"},
        "gmail": {"icon": "mail", "color": "blue"},
        "calendar": {"icon": "calendar", "color": "green"},
        "project-tracker": {"icon": "kanban", "color": "green"},
        "core": {"icon": "activity", "color": "zinc"},
        "notes": {"icon": "file-text", "color": "zinc"},
    }

    for f in sorted(qpack_dir.glob("*.qpack.json")):
        try:
            data = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        module = data.get("module", f.stem)
        style = MODULE_STYLE.get(module, {"icon": "circle", "color": "zinc"})

        for q in data.get("questions", []):
            if q.get("featured"):
                fallbacks.append({
                    "qpack_id": q["id"],
                    "label": q.get("short_label", q["label"]),
                    "icon": style["icon"],
                    "color": style["color"],
                    "priority": 100,  # low priority — these are fallbacks
                    "data_preview": {},
                })

    return fallbacks


# ──────────────────────────────────────────────────────────────────────
#  Main entry point
# ──────────────────────────────────────────────────────────────────────

# Ordered list of suggestion checkers — evaluated top to bottom,
# first N that return non-None are used.
_SUGGESTION_CHECKS = [
    _check_urgent_nudges,        # 1
    _check_imminent_meeting,     # 2
    _check_email_backlog,        # 3
    _check_cold_active_contacts, # 4
    _check_overdue_tasks,        # 5
    _check_stalled_projects,     # 6
    _check_discovery_candidates, # 7
]


def get_smart_suggestions(
    db_path: Path = DB_PATH,
    max_suggestions: int = 3,
) -> list[dict]:
    """
    Compute the most relevant QPack questions to show on the home screen.

    Checks data conditions in priority order, returns the first
    `max_suggestions` that apply. Falls back to featured QPack
    questions if fewer conditions match.

    Returns a list of suggestion dicts, each with:
        qpack_id, label, icon, color, priority, data_preview
    """
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row

    suggestions = []

    try:
        for check_fn in _SUGGESTION_CHECKS:
            if len(suggestions) >= max_suggestions:
                break
            result = check_fn(db)
            if result is not None:
                # Deduplicate by qpack_id
                if not any(s["qpack_id"] == result["qpack_id"] for s in suggestions):
                    suggestions.append(result)
    finally:
        db.close()

    # Fill remaining slots with featured fallbacks
    if len(suggestions) < max_suggestions:
        fallbacks = _get_featured_fallbacks()
        existing_ids = {s["qpack_id"] for s in suggestions}
        for fb in fallbacks:
            if len(suggestions) >= max_suggestions:
                break
            if fb["qpack_id"] not in existing_ids:
                suggestions.append(fb)
                existing_ids.add(fb["qpack_id"])

    return suggestions


# ──────────────────────────────────────────────────────────────────────
#  CLI
# ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    max_count = 3
    as_json = False

    args = sys.argv[1:]
    if "--json" in args:
        as_json = True
        args.remove("--json")
    if "--max" in args:
        idx = args.index("--max")
        if idx + 1 < len(args):
            max_count = int(args[idx + 1])

    suggestions = get_smart_suggestions(max_suggestions=max_count)

    if as_json:
        print(json.dumps(suggestions, indent=2, default=str))
    else:
        print(f"\n{'='*60}")
        print(f"  Smart Suggestions ({len(suggestions)} of {max_count} max)")
        print(f"{'='*60}\n")

        if not suggestions:
            print("  No suggestions — no data conditions matched and no QPacks found.")
        else:
            COLOR_SYMBOLS = {
                "red": "!!!",
                "amber": " ! ",
                "blue": " i ",
                "green": " + ",
                "zinc": " . ",
            }
            for i, s in enumerate(suggestions, 1):
                sym = COLOR_SYMBOLS.get(s["color"], "   ")
                priority = s["priority"]
                preview = s.get("data_preview", {})
                preview_str = ""
                if preview:
                    parts = [f"{k}={v}" for k, v in preview.items() if k != "names"]
                    if parts:
                        preview_str = f"  ({', '.join(parts)})"

                print(f"  {i}. [{sym}] {s['label']}{preview_str}")
                print(f"       qpack: {s['qpack_id']}  |  icon: {s['icon']}  |  priority: {priority}")
        print()
