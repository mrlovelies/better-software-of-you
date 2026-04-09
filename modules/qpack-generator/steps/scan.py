"""
Step 1: Scan — Detect installed modules, computed views, and data state.
"""

import sqlite3
from pathlib import Path
import sys
from pathlib import Path
_mod_dir = Path(__file__).resolve().parents[1]
if str(_mod_dir) not in sys.path:
    sys.path.insert(0, str(_mod_dir))
from pipeline import PipelineStep

DB_PATH = Path.home() / ".local" / "share" / "software-of-you" / "soy.db"

# Tables to count for data state detection
DATA_TABLES = {
    "contacts": {"table": "contacts", "where": "status = 'active'", "module": "crm"},
    "emails": {"table": "emails", "where": "1=1", "module": "gmail"},
    "calendar_events": {"table": "calendar_events", "where": "1=1", "module": "calendar"},
    "projects": {"table": "projects", "where": "status IN ('active','planning')", "module": "project-tracker"},
    "tasks": {"table": "tasks", "where": "1=1", "module": "project-tracker"},
    "commitments": {"table": "commitments_new", "where": "status IN ('open','overdue')", "module": "conversation-intelligence"},
    "interactions": {"table": "contact_interactions", "where": "1=1", "module": "crm"},
    "transcripts": {"table": "transcripts", "where": "1=1", "module": "conversation-intelligence"},
    "decisions": {"table": "decisions", "where": "1=1", "module": "notes"},
    "journal_entries": {"table": "journal_entries", "where": "1=1", "module": "notes"},
    "follow_ups": {"table": "follow_ups", "where": "status = 'pending'", "module": "crm"},
    "notes": {"table": "notes", "where": "1=1", "module": "notes"},
}

# Computed views that QPack context queries target
COMPUTED_VIEWS = [
    "v_contact_health",
    "v_commitment_status",
    "v_nudge_items",
    "v_nudge_summary",
    "v_meeting_prep",
    "v_project_health",
    "v_email_response_queue",
    "v_discovery_candidates",
]


class ScanStep(PipelineStep):
    name = "scan"

    def __call__(self, ctx: dict) -> dict:
        log = ctx["_pipeline"].log
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row

        # Installed modules
        modules = {
            row["name"]: {"version": row["version"], "enabled": bool(row["enabled"])}
            for row in db.execute("SELECT name, version, enabled FROM modules").fetchall()
        }
        enabled = [name for name, info in modules.items() if info["enabled"]]
        log(f"    {len(enabled)} modules enabled: {', '.join(sorted(enabled))}")

        # Data counts
        data_state = {}
        for key, cfg in DATA_TABLES.items():
            try:
                row = db.execute(f"SELECT COUNT(*) as n FROM {cfg['table']} WHERE {cfg['where']}").fetchone()
                data_state[key] = row["n"]
            except sqlite3.OperationalError:
                data_state[key] = 0
        log(f"    data: {', '.join(f'{k}={v}' for k, v in data_state.items() if v > 0)}")

        # Computed views availability
        available_views = []
        for view in COMPUTED_VIEWS:
            try:
                db.execute(f"SELECT 1 FROM {view} LIMIT 1")
                available_views.append(view)
            except sqlite3.OperationalError:
                pass
        log(f"    {len(available_views)}/{len(COMPUTED_VIEWS)} computed views available")

        # View row counts (for filtering)
        view_counts = {}
        for view in available_views:
            try:
                row = db.execute(f"SELECT COUNT(*) as n FROM {view}").fetchone()
                view_counts[view] = row["n"]
            except sqlite3.OperationalError:
                view_counts[view] = 0

        db.close()

        ctx["modules"] = modules
        ctx["enabled_modules"] = enabled
        ctx["data_state"] = data_state
        ctx["available_views"] = available_views
        ctx["view_counts"] = view_counts
        return ctx
