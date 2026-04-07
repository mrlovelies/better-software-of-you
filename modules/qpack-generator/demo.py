#!/usr/bin/env python3
"""
QPack Demo — Run the full pipeline and execute key questions.

This is the one-command demo:
    python3 modules/qpack-generator/demo.py

It generates QPacks, picks smart suggestions, executes 3 representative
questions, and shows formatted output. No LLM required.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline import Pipeline
from steps import ScanStep, TemplateStep, FilterStep, ValidateStep, AdaptStep, DeployStep

DB_PATH = Path.home() / ".local" / "share" / "software-of-you" / "soy.db"
QPACK_DIR = Path(__file__).resolve().parents[2] / "qpacks"


def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def run_demo():
    section("QPack Demo")
    print(f"  Database: {DB_PATH}")
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    # --- Step 1: Generate ---
    section("1. Pipeline: Generate QPacks")
    p = Pipeline([ScanStep(), TemplateStep(), FilterStep(), ValidateStep(), AdaptStep(), DeployStep()])
    ctx = p.run()

    deployed = ctx.get("deployed", [])
    data_state = ctx.get("data_state", {})
    print(f"\n  Result: {sum(d['questions'] for d in deployed)} questions across {len(deployed)} modules")
    print(f"  Data richness: {ctx.get('adapted_templates', {}).get('crm', {}).get('_data_tier', '?')}")

    # --- Step 2: Smart Suggestions ---
    section("2. Smart Suggestions (what the home screen shows)")
    try:
        from suggestions import get_smart_suggestions
        suggestions = get_smart_suggestions()
        if suggestions:
            for i, s in enumerate(suggestions, 1):
                preview = s.get("data_preview", {})
                count = preview.get("count", "")
                count_str = f"  (count={count})" if count else ""
                print(f"  {i}. {s['label']}{count_str}")
                print(f"     qpack: {s['qpack_id']}  |  color: {s['color']}")
        else:
            print("  (no suggestions — data may be sparse)")
    except Exception as e:
        print(f"  Error loading suggestions: {e}")

    # --- Step 3: Execute Questions ---
    section("3. Execute: Sample Questions")

    # Pick 3 questions to demo — prioritize ones with data
    demo_questions = [
        "crm.cold_relationships",     # data_table format, no LLM
        "projects.health_overview",    # data_table format, no LLM
        "nudges.attention_now",        # prioritized_list format, no LLM
    ]

    import sqlite3
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    for qid in demo_questions:
        # Find the question
        target = None
        for f in QPACK_DIR.glob("*.qpack.json"):
            data = json.loads(f.read_text())
            for q in data.get("questions", []):
                if q["id"] == qid:
                    target = q
                    break
            if target:
                break

        if not target:
            print(f"  [{qid}] — not found, skipping")
            continue

        print(f"  --- {target['label']} ---")
        print(f"  ID: {qid}  |  Format: {target.get('answer_format')}  |  LLM: {target.get('requires_llm', False)}")

        for cq in target.get("context_queries", []):
            try:
                rows = db.execute(cq["sql"]).fetchall()
                if rows:
                    cols = rows[0].keys()
                    # Print compact table
                    header = " | ".join(str(c)[:15].ljust(15) for c in cols[:6])
                    print(f"  {header}")
                    print(f"  {'─' * (17 * min(len(cols), 6))}")
                    for row in rows[:5]:
                        vals = [str(row[c] if row[c] is not None else "—")[:15].ljust(15) for c in list(cols)[:6]]
                        print(f"  {' | '.join(vals)}")
                    if len(rows) > 5:
                        print(f"  ... {len(rows) - 5} more rows")
                    print(f"  [{len(rows)} rows total]")
                else:
                    print(f"  (no results)")
            except sqlite3.OperationalError as e:
                print(f"  SQL ERROR: {e}")
        print()

    db.close()

    # --- Step 4: Router Demo ---
    section("4. Router: Natural Language Queries")
    try:
        from router import route_query
        test_queries = [
            "who should I focus on",
            "what's overdue",
            "Jessica",
        ]
        for query in test_queries:
            result = route_query(query)
            match = result.get("matched_question_id", "?")
            conf = int(result.get("confidence", 0) * 100)
            entity = result.get("entity_match")
            entity_str = f"  (entity: {entity['name']})" if entity else ""
            print(f"  \"{query}\"  →  {match} ({conf}% confidence){entity_str}")
    except Exception as e:
        print(f"  Router error: {e}")

    # --- Step 5: Health Check ---
    section("5. Health Check")
    try:
        from steps.health import run_standalone
        report = run_standalone()
        summary = report.get("summary", {})
        print(f"  Errors: {summary.get('errors', 0)}  |  Warnings: {summary.get('warnings', 0)}  |  Info: {summary.get('info', 0)}")
        errors = [i for i in report.get("issues", []) if i["severity"] == "error"]
        if errors:
            for e in errors:
                print(f"  ERROR: {e['message']}")
        else:
            print(f"  No errors found.")
    except Exception as e:
        print(f"  Health check error: {e}")

    # --- Summary ---
    section("Summary")
    print(f"  Pipeline: {len(deployed)} modules, {sum(d['questions'] for d in deployed)} questions")
    print(f"  Data: {data_state.get('contacts', 0)} contacts, {data_state.get('emails', 0)} emails, {data_state.get('projects', 0)} projects")
    print(f"  Computed views: {len(ctx.get('available_views', []))}/8 available")
    print(f"  Output: {QPACK_DIR}/")
    print()
    print(f"  Next steps:")
    print(f"    python3 modules/qpack-generator/serve.py      # Start HTTP API on :8788")
    print(f"    python3 modules/qpack-generator/run.py execute # List all questions")
    print(f"    python3 modules/qpack-generator/router.py \"your question here\"")
    print()


if __name__ == "__main__":
    run_demo()
