#!/usr/bin/env python3
"""
Signal Evolution — Self-improving feedback loops for the harvest pipeline.

Tracks performance at every stage and adapts:
  - Which queries yield viable signals (amplify winners, prune losers)
  - Which subreddits are productive (auto-enable/disable)
  - Which regex patterns produce true vs false positives (refine matching)
  - How well LLM triage aligns with human decisions (recalibrate)
  - Which industries produce shippable products (focus effort)

Usage:
  python3 signal_evolution.py update          # update all stats from current data
  python3 signal_evolution.py report          # show performance report
  python3 signal_evolution.py adapt           # propose and apply adaptations
  python3 signal_evolution.py suggest-queries # LLM-generated new queries from successful signals
  python3 signal_evolution.py suggest-subs    # suggest new subreddits from successful signals
  python3 signal_evolution.py log             # show evolution history
"""

import sys
import os
import json
import sqlite3
import argparse
from urllib.request import Request, urlopen

PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(PLUGIN_ROOT, "data", "soy.db")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://100.91.234.67:11434")
OLLAMA_HOST_14B = os.environ.get("OLLAMA_HOST_14B", "http://100.74.238.16:11434")
EVOLUTION_MODEL = "qwen2.5:14b"

# Thresholds for adaptation
MIN_SAMPLES = 5            # minimum signals before making decisions
LOW_YIELD_THRESHOLD = 0.05  # below 5% yield = consider pruning
HIGH_YIELD_THRESHOLD = 0.3  # above 30% yield = amplify
LOW_PRECISION_THRESHOLD = 0.3  # pattern matching below 30% precision = review
SUBREDDIT_DISABLE_THRESHOLD = 0.02  # below 2% yield after 20+ signals = disable


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def ollama_generate(model, prompt, temperature=0.3):
    host = OLLAMA_HOST_14B if "14b" in model else OLLAMA_HOST
    url = f"{host}/api/generate"
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature},
    }).encode()
    req = Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
            return data.get("response", "").strip()
    except Exception as e:
        print(f"  [error] Ollama call failed: {e}", file=sys.stderr)
        return None


def cmd_update(args):
    """Recompute all stats from current data."""
    db = get_db()

    # --- Subreddit stats ---
    subs = db.execute("""
        SELECT
            s.subreddit,
            COUNT(*) as harvested,
            SUM(CASE WHEN t.verdict = 'approved' THEN 1 ELSE 0 END) as approved,
            SUM(CASE WHEN b.status = 'shipped' THEN 1 ELSE 0 END) as shipped,
            COALESCE(SUM(b.revenue), 0) as revenue,
            AVG(t.composite_score) as avg_score
        FROM harvest_signals s
        LEFT JOIN harvest_triage t ON t.signal_id = s.id
        LEFT JOIN harvest_builds b ON b.triage_id = t.id
        WHERE s.subreddit IS NOT NULL AND s.subreddit != ''
        GROUP BY s.subreddit
    """).fetchall()

    for sub in subs:
        harvested = sub["harvested"] or 0
        approved = sub["approved"] or 0
        yield_rate = approved / harvested if harvested > 0 else 0

        db.execute("""
            INSERT INTO harvest_subreddit_stats
                (subreddit, signals_harvested, signals_approved, signals_shipped,
                 revenue_generated, avg_composite_score, yield_rate, last_harvested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(subreddit) DO UPDATE SET
                signals_harvested = excluded.signals_harvested,
                signals_approved = excluded.signals_approved,
                signals_shipped = excluded.signals_shipped,
                revenue_generated = excluded.revenue_generated,
                avg_composite_score = excluded.avg_composite_score,
                yield_rate = excluded.yield_rate,
                last_harvested_at = excluded.last_harvested_at,
                updated_at = datetime('now')
        """, (sub["subreddit"], harvested, approved, sub["shipped"] or 0,
              sub["revenue"] or 0, sub["avg_score"], yield_rate))

    # --- Industry stats ---
    industries = db.execute("""
        SELECT
            s.industry,
            COUNT(*) as found,
            SUM(CASE WHEN t.verdict = 'approved' THEN 1 ELSE 0 END) as approved,
            SUM(CASE WHEN b.id IS NOT NULL THEN 1 ELSE 0 END) as attempted,
            SUM(CASE WHEN b.status = 'shipped' THEN 1 ELSE 0 END) as shipped,
            COALESCE(SUM(b.revenue), 0) as revenue
        FROM harvest_signals s
        LEFT JOIN harvest_triage t ON t.signal_id = s.id
        LEFT JOIN harvest_builds b ON b.triage_id = t.id
        WHERE s.industry IS NOT NULL AND s.industry != ''
        GROUP BY s.industry
    """).fetchall()

    for ind in industries:
        attempted = ind["attempted"] or 0
        shipped = ind["shipped"] or 0
        success_rate = shipped / attempted if attempted > 0 else 0

        db.execute("""
            INSERT INTO harvest_industry_stats
                (industry, signals_found, signals_approved, builds_attempted,
                 builds_shipped, total_revenue, success_rate)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(industry) DO UPDATE SET
                signals_found = excluded.signals_found,
                signals_approved = excluded.signals_approved,
                builds_attempted = excluded.builds_attempted,
                builds_shipped = excluded.builds_shipped,
                total_revenue = excluded.total_revenue,
                success_rate = excluded.success_rate,
                updated_at = datetime('now')
        """, (ind["industry"], ind["found"], ind["approved"] or 0,
              attempted, shipped, ind["revenue"] or 0, success_rate))

    # --- Triage calibration ---
    # Record human overrides of LLM triage decisions
    overrides = db.execute("""
        SELECT t.signal_id, t.verdict, t.composite_score,
               t.human_reviewed, t.verdict_reason
        FROM harvest_triage t
        WHERE t.human_reviewed = 1
        AND NOT EXISTS (
            SELECT 1 FROM triage_calibration c WHERE c.signal_id = t.signal_id
        )
    """).fetchall()

    for ov in overrides:
        # Determine if the model's initial pass aligned with human decision
        model_would_pass = ov["composite_score"] and ov["composite_score"] >= 5.0
        human_approved = ov["verdict"] == "approved"
        was_correct = 1 if model_would_pass == human_approved else 0

        db.execute("""
            INSERT INTO triage_calibration
                (signal_id, tier, model_verdict, human_verdict, was_correct,
                 composite_score_at_decision)
            VALUES (?, 't2', ?, ?, ?, ?)
        """, (ov["signal_id"],
              "pass" if model_would_pass else "fail",
              ov["verdict"],
              was_correct,
              ov["composite_score"]))

    db.commit()

    # Count what was updated
    sub_count = len(subs)
    ind_count = len(industries)
    cal_count = len(overrides)
    print(f"Stats updated: {sub_count} subreddits, {ind_count} industries, {cal_count} new calibrations")
    db.close()


def cmd_report(args):
    """Show performance report across all dimensions."""
    db = get_db()

    # Overall funnel
    total_signals = db.execute("SELECT COUNT(*) as c FROM harvest_signals").fetchone()["c"]
    passed_t1 = db.execute("SELECT COUNT(*) as c FROM harvest_triage WHERE verdict != 'rejected' OR (verdict = 'rejected' AND verdict_reason NOT LIKE 'T1%')").fetchone()["c"]
    scored = db.execute("SELECT COUNT(*) as c FROM harvest_triage WHERE composite_score IS NOT NULL").fetchone()["c"]
    approved = db.execute("SELECT COUNT(*) as c FROM harvest_triage WHERE verdict = 'approved'").fetchone()["c"]
    built = db.execute("SELECT COUNT(*) as c FROM harvest_builds").fetchone()["c"]
    shipped = db.execute("SELECT COUNT(*) as c FROM harvest_builds WHERE status = 'shipped'").fetchone()["c"]
    revenue = db.execute("SELECT COALESCE(SUM(revenue), 0) as r FROM harvest_builds").fetchone()["r"]

    print("Signal Harvester — Performance Report")
    print("=" * 55)
    print(f"\nFunnel:")
    print(f"  Harvested:    {total_signals}")
    print(f"  Passed T1:    {passed_t1} ({passed_t1/total_signals*100:.0f}%)" if total_signals else "  Passed T1:    0")
    print(f"  Scored (T2):  {scored}")
    print(f"  Approved:     {approved}")
    print(f"  Built:        {built}")
    print(f"  Shipped:      {shipped}")
    print(f"  Revenue:      ${revenue:.2f}")

    # Top subreddits
    subs = db.execute("""
        SELECT * FROM harvest_subreddit_stats
        WHERE signals_harvested >= 1
        ORDER BY yield_rate DESC LIMIT 10
    """).fetchall()

    if subs:
        print(f"\nTop Subreddits (by yield):")
        for s in subs:
            print(f"  r/{s['subreddit']}: {s['signals_harvested']} harvested, "
                  f"{s['signals_approved']} approved, yield {s['yield_rate']*100:.1f}%, "
                  f"avg score {s['avg_composite_score'] or 0:.1f}")

    # Top industries
    inds = db.execute("""
        SELECT * FROM harvest_industry_stats
        WHERE signals_found >= 1
        ORDER BY signals_approved DESC LIMIT 10
    """).fetchall()

    if inds:
        print(f"\nTop Industries:")
        for i in inds:
            print(f"  {i['industry']}: {i['signals_found']} found, "
                  f"{i['signals_approved']} approved, "
                  f"{i['builds_shipped']} shipped, ${i['total_revenue']:.2f}")

    # Triage accuracy
    cal = db.execute("""
        SELECT
            COUNT(*) as total,
            SUM(was_correct) as correct
        FROM triage_calibration
    """).fetchone()

    if cal["total"] > 0:
        accuracy = cal["correct"] / cal["total"] * 100
        print(f"\nTriage Accuracy: {cal['correct']}/{cal['total']} ({accuracy:.0f}%)")

    # Evolution history
    recent = db.execute("""
        SELECT * FROM harvest_evolution_log
        ORDER BY created_at DESC LIMIT 5
    """).fetchall()

    if recent:
        print(f"\nRecent Adaptations:")
        for r in recent:
            print(f"  [{r['stage']}] {r['change_type']}: {r['description']}")

    db.close()


def cmd_adapt(args):
    """Analyze performance and propose/apply adaptations."""
    db = get_db()
    adaptations = []

    print("Analyzing pipeline performance...\n")

    # --- Subreddit adaptations ---
    low_yield_subs = db.execute("""
        SELECT * FROM harvest_subreddit_stats
        WHERE signals_harvested >= ? AND yield_rate < ? AND active = 1
    """, (MIN_SAMPLES, SUBREDDIT_DISABLE_THRESHOLD)).fetchall()

    for sub in low_yield_subs:
        adaptations.append({
            "stage": "harvest",
            "type": "subreddit_disabled",
            "desc": f"Disable r/{sub['subreddit']} — {sub['signals_harvested']} signals, "
                    f"{sub['yield_rate']*100:.1f}% yield (below {SUBREDDIT_DISABLE_THRESHOLD*100}% threshold)",
            "action": lambda s=sub: db.execute(
                "UPDATE harvest_subreddit_stats SET active = 0, updated_at = datetime('now') WHERE subreddit = ?",
                (s["subreddit"],)
            ),
        })

    high_yield_subs = db.execute("""
        SELECT * FROM harvest_subreddit_stats
        WHERE signals_harvested >= ? AND yield_rate > ?
    """, (MIN_SAMPLES, HIGH_YIELD_THRESHOLD)).fetchall()

    for sub in high_yield_subs:
        adaptations.append({
            "stage": "harvest",
            "type": "subreddit_amplified",
            "desc": f"Amplify r/{sub['subreddit']} — {sub['yield_rate']*100:.1f}% yield, "
                    f"avg score {sub['avg_composite_score'] or 0:.1f}. Increase query depth.",
            "action": None,  # informational — harvester reads this
        })

    # --- Triage weight adaptations ---
    cal_data = db.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN was_correct = 1 THEN 1 ELSE 0 END) as correct,
            AVG(CASE WHEN human_verdict = 'approved' THEN composite_score_at_decision END) as avg_approved_score,
            AVG(CASE WHEN human_verdict = 'rejected' THEN composite_score_at_decision END) as avg_rejected_score
        FROM triage_calibration
    """).fetchone()

    if cal_data["total"] and cal_data["total"] >= MIN_SAMPLES:
        accuracy = cal_data["correct"] / cal_data["total"] * 100
        if accuracy < 70:
            adaptations.append({
                "stage": "triage",
                "type": "accuracy_warning",
                "desc": f"Triage accuracy is {accuracy:.0f}% ({cal_data['correct']}/{cal_data['total']}). "
                        f"Avg approved score: {cal_data['avg_approved_score'] or 0:.1f}, "
                        f"avg rejected: {cal_data['avg_rejected_score'] or 0:.1f}. "
                        f"Consider adjusting composite weights or approval threshold.",
                "action": None,
            })

    # --- Industry focus ---
    hot_industries = db.execute("""
        SELECT * FROM harvest_industry_stats
        WHERE signals_approved >= 2
        ORDER BY signals_approved DESC LIMIT 3
    """).fetchall()

    for ind in hot_industries:
        adaptations.append({
            "stage": "harvest",
            "type": "industry_focus",
            "desc": f"Industry '{ind['industry']}' has {ind['signals_approved']} approved signals. "
                    f"Consider targeted harvest queries for this vertical.",
            "action": None,
        })

    # --- Report and optionally apply ---
    if not adaptations:
        print("No adaptations needed yet. Need more data flowing through the pipeline.")
        print(f"  Current thresholds: min {MIN_SAMPLES} samples per source before adapting.")
        db.close()
        return

    print(f"Proposed adaptations ({len(adaptations)}):")
    print("=" * 60)

    for i, a in enumerate(adaptations):
        print(f"\n  [{a['stage'].upper()}] {a['type']}")
        print(f"  {a['desc']}")

        if a.get("action") and (args.apply if hasattr(args, 'apply') else False):
            a["action"]()
            db.execute("""
                INSERT INTO harvest_evolution_log
                    (stage, change_type, description, reason)
                VALUES (?, ?, ?, 'Auto-adapted based on pipeline performance data')
            """, (a["stage"], a["type"], a["desc"]))
            print(f"  → APPLIED")

    if not (hasattr(args, 'apply') and args.apply):
        print(f"\nRun with --apply to execute these adaptations.")

    db.commit()
    db.close()


def cmd_suggest_queries(args):
    """Use LLM to generate new search queries based on successful signals."""
    db = get_db()

    # Get the most successful signals (approved or high-scoring)
    good_signals = db.execute("""
        SELECT s.raw_text, s.extracted_pain, s.industry, s.subreddit, t.composite_score
        FROM harvest_signals s
        JOIN harvest_triage t ON t.signal_id = s.id
        WHERE t.verdict = 'approved' OR t.composite_score >= 6.0
        ORDER BY t.composite_score DESC
        LIMIT 10
    """).fetchall()

    if not good_signals:
        print("Need more approved/high-scoring signals to generate query suggestions.")
        print("Run the pipeline first and approve some signals.")
        return

    signal_summaries = "\n".join([
        f"- [{row['industry'] or '?'}] {row['extracted_pain'] or row['raw_text'][:150]}"
        for row in good_signals
    ])

    prompt = f"""Based on these successful product pain-point signals that were found on Reddit, suggest 10 NEW search queries that would find SIMILAR types of problems. Focus on the patterns that made these successful — the kinds of frustrations, the domains, the way people express unmet needs.

Successful signals:
{signal_summaries}

Return ONLY a JSON array of 10 search query strings. No explanation.
Example: ["query one", "query two", ...]

JSON:"""

    print("Generating new search queries from successful signal patterns...")
    response = ollama_generate(EVOLUTION_MODEL, prompt)

    if not response:
        print("LLM call failed.")
        return

    try:
        # Parse JSON from response
        cleaned = response.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
        start = cleaned.find("[")
        end = cleaned.rfind("]") + 1
        queries = json.loads(cleaned[start:end])

        print(f"\nSuggested new queries ({len(queries)}):")
        for i, q in enumerate(queries):
            print(f"  {i+1}. \"{q}\"")

        print(f"\nTo add these to the harvester, update the SEARCH_QUERIES list in signal_harvester.py")
        print(f"Or run a targeted harvest: python3 signal_harvester.py harvest --target=\"<topic>\"")

    except (json.JSONDecodeError, ValueError) as e:
        print(f"Failed to parse suggestions: {e}")
        print(f"Raw response: {response[:500]}")

    db.close()


def cmd_suggest_subs(args):
    """Suggest new subreddits based on successful signal industries."""
    db = get_db()

    industries = db.execute("""
        SELECT industry, COUNT(*) as count
        FROM harvest_signals s
        JOIN harvest_triage t ON t.signal_id = s.id
        WHERE (t.verdict = 'approved' OR t.composite_score >= 5.5)
        AND s.industry IS NOT NULL
        GROUP BY s.industry
        ORDER BY count DESC
        LIMIT 5
    """).fetchall()

    if not industries:
        print("Need more triaged signals to suggest subreddits.")
        return

    industry_list = ", ".join([f"{row['industry']} ({row['count']} signals)" for row in industries])

    prompt = f"""Given these industries where people have expressed unmet product needs: {industry_list}

Suggest 15 Reddit subreddits (just the name, no r/ prefix) where people in these industries are likely to complain about tools, wish for better solutions, or ask for software recommendations. Include both broad and niche subreddits.

Return ONLY a JSON array of subreddit names. No explanation.
Example: ["subreddit1", "subreddit2", ...]

JSON:"""

    print("Suggesting new subreddits based on successful signal industries...")
    response = ollama_generate(EVOLUTION_MODEL, prompt)

    if not response:
        print("LLM call failed.")
        return

    try:
        cleaned = response.strip()
        start = cleaned.find("[")
        end = cleaned.rfind("]") + 1
        subs = json.loads(cleaned[start:end])

        # Check which are already in our list
        existing = db.execute("SELECT subreddit FROM harvest_subreddit_stats").fetchall()
        existing_set = {r["subreddit"].lower() for r in existing}

        print(f"\nSuggested subreddits ({len(subs)}):")
        for s in subs:
            status = " (already tracking)" if s.lower() in existing_set else " NEW"
            print(f"  r/{s}{status}")

    except (json.JSONDecodeError, ValueError) as e:
        print(f"Failed to parse suggestions: {e}")

    db.close()


def cmd_log(args):
    """Show evolution history."""
    db = get_db()

    rows = db.execute("""
        SELECT * FROM harvest_evolution_log
        ORDER BY created_at DESC
        LIMIT ?
    """, (args.limit if hasattr(args, 'limit') else 20,)).fetchall()

    if not rows:
        print("No evolution events recorded yet.")
        print("Run 'update' to compute stats, then 'adapt' to generate adaptations.")
        return

    print("Evolution Log")
    print("=" * 60)
    for row in rows:
        print(f"\n  [{row['created_at']}] {row['stage'].upper()} — {row['change_type']}")
        print(f"  {row['description']}")
        if row['reason']:
            print(f"  Reason: {row['reason']}")

    db.close()


def main():
    parser = argparse.ArgumentParser(description="Signal Evolution — self-improving harvest pipeline")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("update", help="Recompute all stats from current data")
    subparsers.add_parser("report", help="Show performance report")

    p_adapt = subparsers.add_parser("adapt", help="Propose and apply adaptations")
    p_adapt.add_argument("--apply", action="store_true", help="Actually apply proposed changes")

    subparsers.add_parser("suggest-queries", help="LLM-generated new queries from successful signals")
    subparsers.add_parser("suggest-subs", help="Suggest new subreddits from successful industries")

    p_log = subparsers.add_parser("log", help="Show evolution history")
    p_log.add_argument("--limit", type=int, default=20)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    commands = {
        "update": cmd_update,
        "report": cmd_report,
        "adapt": cmd_adapt,
        "suggest-queries": cmd_suggest_queries,
        "suggest-subs": cmd_suggest_subs,
        "log": cmd_log,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
