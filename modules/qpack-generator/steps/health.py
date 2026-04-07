"""
Health Check Pipeline Step — Detect issues in generated QPacks.

Checks:
  1. Schema drift — verify referenced tables/views/columns still exist
  2. Empty data — flag questions where ALL context queries return 0 rows
  3. Stale QPacks — files on disk older than latest migration
  4. Missing templates — enabled modules without a QPack template
  5. Orphaned QPacks — QPack files referencing disabled/missing modules
  6. Duplicate question IDs — ID collisions across QPack files
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


def _get_db_tables(db: sqlite3.Connection) -> set:
    """Return all table and view names in the database."""
    rows = db.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
    ).fetchall()
    return {row[0] for row in rows}


def _get_table_columns(db: sqlite3.Connection, table: str) -> set:
    """Return column names for a table or view."""
    try:
        cursor = db.execute(f"PRAGMA table_info({table})")
        cols = {row[1] for row in cursor.fetchall()}
        if cols:
            return cols
        # PRAGMA table_info returns nothing for views — try a LIMIT 0 query
        cursor = db.execute(f"SELECT * FROM {table} LIMIT 0")
        return {desc[0] for desc in cursor.description} if cursor.description else set()
    except sqlite3.OperationalError:
        return set()


def _latest_migration_mtime(plugin_root: Path) -> float:
    """Return the mtime of the newest migration file, or 0 if none found."""
    migrations_dir = plugin_root / "data" / "migrations"
    if not migrations_dir.exists():
        return 0.0
    mtimes = [f.stat().st_mtime for f in migrations_dir.glob("*.sql")]
    return max(mtimes) if mtimes else 0.0


class HealthStep(PipelineStep):
    """Run health checks on generated QPack files and populate a health_report."""

    name = "health"

    def __call__(self, ctx: dict) -> dict:
        log = ctx["_pipeline"].log
        issues = []

        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row

        known_tables = _get_db_tables(db)

        # Column cache: table_name -> set of column names
        column_cache: dict[str, set] = {}

        def get_columns(table: str) -> set:
            if table not in column_cache:
                column_cache[table] = _get_table_columns(db, table)
            return column_cache[table]

        # Load all QPack files from disk
        qpacks: dict[str, dict] = {}
        if QPACK_DIR.exists():
            for f in sorted(QPACK_DIR.glob("*.qpack.json")):
                try:
                    data = json.loads(f.read_text())
                    module_name = data.get("module", f.stem.replace(".qpack", ""))
                    qpacks[module_name] = {"data": data, "path": f}
                except (json.JSONDecodeError, OSError) as e:
                    issues.append({
                        "severity": "error",
                        "category": "schema_drift",
                        "qpack_module": f.stem,
                        "question_id": None,
                        "query_key": None,
                        "message": f"Failed to parse {f.name}: {e}",
                        "auto_fixable": False,
                    })

        # -- Check 1: Schema drift --
        log("    checking schema drift...")
        for module_name, qp in qpacks.items():
            for q in qp["data"].get("questions", []):
                q_id = q.get("id", "unknown")
                for cq in q.get("context_queries", []):
                    sql = cq.get("sql", "")
                    key = cq.get("key", "unknown")
                    try:
                        # Run with LIMIT 0 to validate SQL without fetching data
                        db.execute(f"SELECT * FROM ({sql}) _hc LIMIT 0")
                    except sqlite3.OperationalError as e:
                        err_str = str(e)
                        auto_fixable = "no such column" in err_str or "no such table" in err_str
                        issues.append({
                            "severity": "error",
                            "category": "schema_drift",
                            "qpack_module": module_name,
                            "question_id": q_id,
                            "query_key": key,
                            "message": err_str,
                            "auto_fixable": auto_fixable,
                        })

        # -- Check 2: Empty data detection --
        log("    checking empty data...")
        for module_name, qp in qpacks.items():
            for q in qp["data"].get("questions", []):
                q_id = q.get("id", "unknown")
                queries = q.get("context_queries", [])
                if not queries:
                    continue
                all_empty = True
                for cq in queries:
                    sql = cq.get("sql", "")
                    try:
                        row = db.execute(f"SELECT COUNT(*) as n FROM ({sql}) _ec").fetchone()
                        if row and row["n"] > 0:
                            all_empty = False
                            break
                    except sqlite3.OperationalError:
                        # Schema drift already caught above — skip here
                        all_empty = False
                        break

                if all_empty:
                    issues.append({
                        "severity": "warning",
                        "category": "empty_data",
                        "qpack_module": module_name,
                        "question_id": q_id,
                        "query_key": None,
                        "message": f"All context queries return 0 rows — answer will be empty",
                        "auto_fixable": False,
                    })

        # -- Check 3: Stale QPacks --
        log("    checking staleness...")
        plugin_root = Path(__file__).resolve().parents[3]
        latest_migration = _latest_migration_mtime(plugin_root)
        if latest_migration > 0:
            for module_name, qp in qpacks.items():
                qpack_mtime = qp["path"].stat().st_mtime
                if qpack_mtime < latest_migration:
                    issues.append({
                        "severity": "warning",
                        "category": "stale_qpack",
                        "qpack_module": module_name,
                        "question_id": None,
                        "query_key": None,
                        "message": f"QPack file is older than latest migration — regenerate recommended",
                        "auto_fixable": True,
                    })

        # -- Check 4: Missing templates --
        log("    checking missing templates...")
        try:
            enabled_modules = {
                row["name"]
                for row in db.execute(
                    "SELECT name FROM modules WHERE enabled = 1"
                ).fetchall()
            }
        except sqlite3.OperationalError:
            enabled_modules = set()

        template_dir = _mod_dir / "templates"
        template_modules = set()
        if template_dir.exists():
            for f in template_dir.glob("*.json"):
                try:
                    data = json.loads(f.read_text())
                    template_modules.add(data.get("module", f.stem))
                except (json.JSONDecodeError, OSError):
                    pass

        for mod in enabled_modules:
            # "core" is a synthetic module that always has a template
            if mod not in template_modules and mod != "core":
                issues.append({
                    "severity": "info",
                    "category": "missing_template",
                    "qpack_module": mod,
                    "question_id": None,
                    "query_key": None,
                    "message": f"Enabled module '{mod}' has no QPack template",
                    "auto_fixable": False,
                })

        # -- Check 5: Orphaned QPacks --
        log("    checking orphaned QPacks...")
        for module_name in qpacks:
            if module_name == "core":
                continue
            if module_name not in enabled_modules:
                issues.append({
                    "severity": "warning",
                    "category": "orphaned",
                    "qpack_module": module_name,
                    "question_id": None,
                    "query_key": None,
                    "message": f"QPack references module '{module_name}' which is not enabled",
                    "auto_fixable": True,
                })

        # -- Check 6: Duplicate question IDs --
        log("    checking duplicate IDs...")
        seen_ids: dict[str, str] = {}  # question_id -> module_name
        for module_name, qp in qpacks.items():
            for q in qp["data"].get("questions", []):
                q_id = q.get("id", "unknown")
                if q_id in seen_ids:
                    issues.append({
                        "severity": "error",
                        "category": "duplicate_id",
                        "qpack_module": module_name,
                        "question_id": q_id,
                        "query_key": None,
                        "message": f"Duplicate question ID — also in '{seen_ids[q_id]}'",
                        "auto_fixable": False,
                    })
                else:
                    seen_ids[q_id] = module_name

        db.close()

        # Build summary
        summary = {
            "errors": sum(1 for i in issues if i["severity"] == "error"),
            "warnings": sum(1 for i in issues if i["severity"] == "warning"),
            "info": sum(1 for i in issues if i["severity"] == "info"),
            "auto_fixable": sum(1 for i in issues if i["auto_fixable"]),
        }

        health_report = {
            "issues": issues,
            "summary": summary,
            "checked_at": datetime.now().isoformat(),
            "qpacks_checked": len(qpacks),
            "questions_checked": sum(
                len(qp["data"].get("questions", [])) for qp in qpacks.values()
            ),
        }

        log(f"    {summary['errors']} errors, {summary['warnings']} warnings, {summary['info']} info")
        if summary["auto_fixable"]:
            log(f"    {summary['auto_fixable']} issues are auto-fixable (regenerate QPacks)")

        ctx["health_report"] = health_report
        return ctx


def run_standalone() -> dict:
    """Run health checks outside the pipeline context (for CLI/API use)."""
    from pipeline import Pipeline

    p = Pipeline([HealthStep()])
    # Provide a minimal context — health step doesn't depend on prior steps
    ctx = p.run()
    return ctx.get("health_report", {})


if __name__ == "__main__":
    report = run_standalone()
    summary = report.get("summary", {})
    issues = report.get("issues", [])

    print(f"\nQPack Health Report")
    print(f"{'='*50}")
    print(f"  QPacks checked: {report.get('qpacks_checked', 0)}")
    print(f"  Questions checked: {report.get('questions_checked', 0)}")
    print(f"  Errors: {summary.get('errors', 0)}")
    print(f"  Warnings: {summary.get('warnings', 0)}")
    print(f"  Info: {summary.get('info', 0)}")
    print(f"  Auto-fixable: {summary.get('auto_fixable', 0)}")

    if issues:
        print(f"\nIssues:")
        for i in issues:
            sev = i["severity"].upper()
            cat = i["category"]
            mod = i["qpack_module"]
            qid = i.get("question_id") or ""
            msg = i["message"]
            fix = " [auto-fixable]" if i["auto_fixable"] else ""
            print(f"  [{sev:7s}] {cat:20s} {mod}/{qid}: {msg}{fix}")
    else:
        print(f"\n  All checks passed.")
    print()
