#!/usr/bin/env python3
"""
QPack Generator — Adaptive question bundle pipeline for SoY GUI.

Scans installed modules and data state, loads question templates,
filters by data availability, validates context queries, adapts
for onboarding states, and deploys QPack JSON files.

Usage:
    python3 modules/qpack-generator/run.py generate     # Full pipeline run
    python3 modules/qpack-generator/run.py scan          # Scan only (dry run)
    python3 modules/qpack-generator/run.py validate      # Scan + validate queries
    python3 modules/qpack-generator/run.py status        # Show current QPack state
    python3 modules/qpack-generator/run.py execute <id>  # Execute a QPack question and show results
"""

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path.home() / ".local" / "share" / "software-of-you" / "soy.db"
QPACK_DIR = PLUGIN_ROOT / "qpacks"

# Allow importing the module
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline import Pipeline
from steps import ScanStep, TemplateStep, FilterStep, ValidateStep, AdaptStep, DeployStep


def cmd_generate():
    """Full pipeline: scan → template → filter → validate → adapt → deploy."""
    print(f"\n{'='*60}")
    print(f"  QPACK GENERATOR — Full Pipeline")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}\n")

    p = Pipeline([
        ScanStep(),
        TemplateStep(),
        FilterStep(),
        ValidateStep(),
        AdaptStep(),
        DeployStep(),
    ])

    ctx = p.run()

    # Summary
    deployed = ctx.get("deployed", [])
    errors = ctx.get("validation_errors", [])
    print(f"\n{'─'*60}")
    print(f"  Summary:")
    print(f"    Modules: {len(deployed)}")
    print(f"    Questions: {sum(d['questions'] for d in deployed)}")
    print(f"    Errors: {len(errors)}")
    print(f"    Output: {ctx.get('deploy_dir', '?')}")
    print(f"{'─'*60}\n")

    return ctx


def cmd_scan():
    """Scan only — show data state without generating."""
    print(f"\n{'='*60}")
    print(f"  QPACK GENERATOR — Scan")
    print(f"{'='*60}\n")

    p = Pipeline([ScanStep(), TemplateStep()])
    ctx = p.run()

    print(f"\n  Data State:")
    for key, count in sorted(ctx["data_state"].items(), key=lambda x: -x[1]):
        bar = "█" * min(count // 2, 30)
        print(f"    {key:20s} {count:5d}  {bar}")

    print(f"\n  Computed Views:")
    for view in ctx["available_views"]:
        count = ctx["view_counts"].get(view, 0)
        print(f"    {view:30s} {count:5d} rows")

    print(f"\n  Templates loaded: {len(ctx['templates'])}")
    for name, t in ctx["templates"].items():
        print(f"    {name}: {len(t.get('questions', []))} questions")
    print()


def cmd_validate():
    """Scan + filter + validate — test all queries without deploying."""
    print(f"\n{'='*60}")
    print(f"  QPACK GENERATOR — Validate")
    print(f"{'='*60}\n")

    p = Pipeline([ScanStep(), TemplateStep(), FilterStep(), ValidateStep()])
    ctx = p.run()

    errors = ctx.get("validation_errors", [])
    if errors:
        print(f"\n  Validation Errors:")
        for e in errors:
            print(f"    {e}")
    else:
        print(f"\n  All context queries passed validation.")

    templates = ctx.get("validated_templates", {})
    print(f"\n  Validated Questions:")
    for name, t in templates.items():
        for q in t.get("questions", []):
            has_data = q.get("_has_data", False)
            counts = q.get("_row_counts", {})
            status = "HAS DATA" if has_data else "EMPTY"
            print(f"    [{status:8s}] {q['id']:40s} {counts}")
    print()


def cmd_status():
    """Show current QPack state from last generation."""
    print(f"\n{'='*60}")
    print(f"  QPACK GENERATOR — Status")
    print(f"{'='*60}\n")

    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    try:
        last_gen = db.execute("SELECT value FROM soy_meta WHERE key = 'qpacks_last_generated'").fetchone()
        manifest_row = db.execute("SELECT value FROM soy_meta WHERE key = 'qpacks_manifest'").fetchone()
    except sqlite3.OperationalError:
        print(f"  Database not initialized — run bootstrap first")
        db.close()
        return

    if last_gen:
        print(f"  Last generated: {last_gen['value']}")
    else:
        print(f"  Never generated — run 'generate' first")
        db.close()
        return

    if manifest_row:
        manifest = json.loads(manifest_row["value"])
        print(f"  Total questions: {manifest.get('total_questions', '?')}")
        print(f"\n  QPacks:")
        for qp in manifest.get("qpacks", []):
            print(f"    {qp['module']:25s} {qp['questions']:3d} questions  ({qp['file']})")

    # Check QPack files on disk
    print(f"\n  Files in qpacks/:")
    if QPACK_DIR.exists():
        for f in sorted(QPACK_DIR.glob("*.json")):
            size = f.stat().st_size
            print(f"    {f.name:35s} {size:6d} bytes")
    else:
        print(f"    (directory not found)")

    db.close()
    print()


def cmd_execute(question_id: str):
    """Execute a specific QPack question and show the raw results."""
    if not QPACK_DIR.exists():
        print("No QPacks generated yet — run 'generate' first")
        return

    # Find the question across all QPack files
    target = None
    for f in QPACK_DIR.glob("*.qpack.json"):
        data = json.loads(f.read_text())
        for q in data.get("questions", []):
            if q["id"] == question_id:
                target = q
                break
        if target:
            break

    if not target:
        print(f"Question '{question_id}' not found. Available questions:")
        for f in QPACK_DIR.glob("*.qpack.json"):
            data = json.loads(f.read_text())
            for q in data.get("questions", []):
                featured = " *" if q.get("featured") else ""
                llm = " [LLM]" if q.get("requires_llm") else ""
                print(f"  {q['id']:45s} {q['label']}{featured}{llm}")
        return

    print(f"\n{'='*60}")
    print(f"  Executing: {target['label']}")
    print(f"  ID: {target['id']}")
    print(f"  Format: {target.get('answer_format', '?')}")
    print(f"  Requires LLM: {target.get('requires_llm', False)}")
    print(f"{'='*60}\n")

    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    for cq in target.get("context_queries", []):
        key = cq["key"]
        sql = cq["sql"]
        print(f"  --- {key} ---")
        print(f"  SQL: {sql[:120]}{'...' if len(sql) > 120 else ''}\n")
        try:
            rows = db.execute(sql).fetchall()
            if rows:
                # Print as table
                cols = rows[0].keys()
                print(f"  {' | '.join(str(c)[:20].ljust(20) for c in cols)}")
                print(f"  {'─' * (22 * len(cols))}")
                for row in rows[:15]:
                    vals = [str(row[c] if row[c] is not None else "—")[:20].ljust(20) for c in cols]
                    print(f"  {' | '.join(vals)}")
                if len(rows) > 15:
                    print(f"  ... and {len(rows) - 15} more rows")
            else:
                print(f"  (no results)")
            print(f"  [{len(rows)} rows]\n")
        except sqlite3.OperationalError as e:
            print(f"  ERROR: {e}\n")

    # If it has a static answer (onboarding), show that
    if "static_answer" in target:
        print(f"  --- Static Answer ---")
        for section, text in target["static_answer"].items():
            print(f"  [{section}] {text}")

    # If it has a prompt template, show the assembled prompt
    if "prompt_template" in target and not target.get("requires_llm"):
        print(f"  --- Prompt Template (not executed — requires LLM) ---")
        print(f"  {target['prompt_template'][:300]}...")

    db.close()
    print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 run.py [generate|scan|validate|status|execute <question_id>]")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "generate":
        cmd_generate()
    elif cmd == "scan":
        cmd_scan()
    elif cmd == "validate":
        cmd_validate()
    elif cmd == "status":
        cmd_status()
    elif cmd == "execute":
        if len(sys.argv) < 3:
            # List all available questions
            cmd_execute("")
        else:
            cmd_execute(sys.argv[2])
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
