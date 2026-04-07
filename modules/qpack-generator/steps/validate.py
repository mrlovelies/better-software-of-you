"""
Step 4: Validate — Test each question's context queries against the live database.
"""

import sqlite3
from pathlib import Path
import sys
_mod_dir = Path(__file__).resolve().parents[1]
if str(_mod_dir) not in sys.path:
    sys.path.insert(0, str(_mod_dir))
from pipeline import PipelineStep

DB_PATH = Path.home() / ".local" / "share" / "software-of-you" / "soy.db"


class ValidateStep(PipelineStep):
    name = "validate"

    def __call__(self, ctx: dict) -> dict:
        log = ctx["_pipeline"].log
        templates = ctx["filtered_templates"]

        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row

        validated = {}
        total_queries = 0
        passed_queries = 0
        failed_queries = 0
        validation_errors = []

        for module_name, template in templates.items():
            valid_questions = []
            for q in template.get("questions", []):
                q_valid = True
                q_row_counts = {}

                for cq in q.get("context_queries", []):
                    sql = cq.get("sql", "")
                    key = cq.get("key", "unknown")
                    total_queries += 1

                    try:
                        # Validate SQL correctness without fetching all data
                        db.execute(f"SELECT * FROM ({sql}) _v LIMIT 0")
                        count_row = db.execute(f"SELECT COUNT(*) as n FROM ({sql}) _c").fetchone()
                        q_row_counts[key] = count_row["n"]
                        passed_queries += 1
                    except sqlite3.OperationalError as e:
                        error_msg = f"{module_name}/{q['id']}/{key}: {e}"
                        validation_errors.append(error_msg)
                        failed_queries += 1
                        q_valid = False
                        log(f"    FAIL: {error_msg}")

                if q_valid:
                    q["_row_counts"] = q_row_counts
                    q["_has_data"] = any(v > 0 for v in q_row_counts.values())
                    valid_questions.append(q)

            if valid_questions:
                t = dict(template)
                t["questions"] = valid_questions
                validated[module_name] = t

        db.close()

        log(f"    {passed_queries}/{total_queries} context queries passed, {failed_queries} failed")
        if validation_errors:
            log(f"    {len(validation_errors)} errors logged")

        ctx["validated_templates"] = validated
        ctx["validation_errors"] = validation_errors
        return ctx
