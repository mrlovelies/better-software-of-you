"""
Step 6: Deploy — Write QPack JSON files and register in database.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
import sys
_mod_dir = Path(__file__).resolve().parents[1]
if str(_mod_dir) not in sys.path:
    sys.path.insert(0, str(_mod_dir))
from pipeline import PipelineStep

DB_PATH = Path.home() / ".local" / "share" / "software-of-you" / "soy.db"
QPACK_DIR = Path(__file__).resolve().parents[3] / "qpacks"


class DeployStep(PipelineStep):
    name = "deploy"

    def __call__(self, ctx: dict) -> dict:
        log = ctx["_pipeline"].log
        templates = ctx["adapted_templates"]

        QPACK_DIR.mkdir(parents=True, exist_ok=True)

        deployed = []
        total_questions = 0

        for module_name, template in templates.items():
            # Clean internal metadata before writing
            output = dict(template)
            questions = []
            for q in output.get("questions", []):
                clean_q = {k: v for k, v in q.items() if not k.startswith("_")}
                questions.append(clean_q)
                total_questions += 1
            output["questions"] = questions
            output["generated_at"] = datetime.now().isoformat()
            output["data_tier"] = template.get("_data_tier", "unknown")

            # Write QPack JSON
            filename = f"{module_name}.qpack.json"
            filepath = QPACK_DIR / filename
            filepath.write_text(json.dumps(output, indent=2, ensure_ascii=False))
            deployed.append({"module": module_name, "file": filename, "questions": len(questions)})

        # Write a manifest of all generated QPacks
        manifest = {
            "generated_at": datetime.now().isoformat(),
            "qpacks": deployed,
            "total_questions": total_questions,
            "data_state": ctx.get("data_state", {}),
        }
        (QPACK_DIR / "_manifest.json").write_text(json.dumps(manifest, indent=2))

        # Register in database
        db = sqlite3.connect(DB_PATH)
        try:
            db.execute("""
                INSERT OR REPLACE INTO soy_meta (key, value, updated_at)
                VALUES ('qpacks_last_generated', ?, datetime('now'))
            """, (datetime.now().isoformat(),))
            db.execute("""
                INSERT OR REPLACE INTO soy_meta (key, value, updated_at)
                VALUES ('qpacks_manifest', ?, datetime('now'))
            """, (json.dumps(manifest),))
            db.execute("""
                INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
                VALUES ('system', 0, 'qpacks_generated', ?, datetime('now'))
            """, (json.dumps({"modules": len(deployed), "questions": total_questions}),))
            db.commit()
        finally:
            db.close()

        log(f"    {len(deployed)} QPack files written to qpacks/")
        log(f"    {total_questions} total questions deployed")
        for d in deployed:
            log(f"      {d['module']}: {d['questions']} questions")

        ctx["deployed"] = deployed
        ctx["deploy_dir"] = str(QPACK_DIR)
        return ctx
