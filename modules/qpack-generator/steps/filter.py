"""
Step 3: Filter — Drop questions whose context queries would return empty results.
"""

import sqlite3
from pathlib import Path
import sys
_mod_dir = Path(__file__).resolve().parents[1]
if str(_mod_dir) not in sys.path:
    sys.path.insert(0, str(_mod_dir))
from pipeline import PipelineStep

DB_PATH = Path.home() / ".local" / "share" / "software-of-you" / "soy.db"

# Minimum data thresholds for question categories
THRESHOLDS = {
    "contacts": 1,
    "emails": 5,
    "calendar_events": 1,
    "projects": 1,
    "tasks": 1,
    "commitments": 1,
    "interactions": 1,
    "transcripts": 1,
    "decisions": 1,
    "journal_entries": 1,
    "follow_ups": 1,
}


class FilterStep(PipelineStep):
    name = "filter"

    def __call__(self, ctx: dict) -> dict:
        log = ctx["_pipeline"].log
        templates = ctx["templates"]
        data_state = ctx["data_state"]
        available_views = set(ctx["available_views"])

        filtered_templates = {}
        total_questions = 0
        kept_questions = 0
        dropped_questions = 0

        for module_name, template in templates.items():
            questions = template.get("questions", [])
            total_questions += len(questions)

            kept = []
            for q in questions:
                # Check data dependencies
                deps = q.get("data_requires", {})
                skip = False
                for table, min_count in deps.items():
                    actual = data_state.get(table, 0)
                    if actual < min_count:
                        skip = True
                        break

                # Check view dependencies
                if not skip:
                    for cq in q.get("context_queries", []):
                        sql = cq.get("sql", "")
                        for view in ["v_contact_health", "v_commitment_status", "v_nudge_items",
                                     "v_nudge_summary", "v_meeting_prep", "v_project_health",
                                     "v_email_response_queue", "v_discovery_candidates"]:
                            if view in sql and view not in available_views:
                                skip = True
                                break
                        if skip:
                            break

                if skip:
                    dropped_questions += 1
                else:
                    kept.append(q)
                    kept_questions += 1

            if kept:
                filtered = dict(template)
                filtered["questions"] = kept
                filtered_templates[module_name] = filtered

        log(f"    {kept_questions}/{total_questions} questions kept, {dropped_questions} dropped (insufficient data)")
        log(f"    {len(filtered_templates)} modules with active questions")

        ctx["filtered_templates"] = filtered_templates
        return ctx
