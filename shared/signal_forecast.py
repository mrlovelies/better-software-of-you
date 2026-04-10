#!/usr/bin/env python3
"""
Signal Forecast — Creative product ideation from pipeline intelligence.

Analyzes patterns in harvested signals, triage outcomes, and industry data
to generate product ideas that nobody is directly asking for. Focuses on
autonomy — how hands-off can the solution be?

Forecasting modes:
  pattern    — synthesize meta-trends from approved/high-scoring signals
  silence    — find gaps where problems exist but nobody's asking for solutions
  adjacent   — find problems next to solved ones that remain unsolved
  upstream   — find root causes behind clusters of symptoms
  automate   — find manual processes that could be fully automated
  creative   — unconstrained ideation combining all modes

Usage:
  python3 signal_forecast.py generate [--mode=creative] [--count=5]
  python3 signal_forecast.py list [--status=idea] [--min-autonomy=7]
  python3 signal_forecast.py evaluate <forecast_id>
  python3 signal_forecast.py approve <forecast_id> [--notes=...]
  python3 signal_forecast.py kill <forecast_id> [reason]
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
FORECAST_MODEL = "qwen2.5:14b"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def ollama_generate(model, prompt, temperature=0.7):
    """Higher temperature for creative generation."""
    host = OLLAMA_HOST_14B if "14b" in model else OLLAMA_HOST
    url = f"{host}/api/generate"
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": 4096},
    }).encode()
    req = Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=600) as resp:
            data = json.loads(resp.read().decode())
            return data.get("response", "").strip()
    except Exception as e:
        print(f"  [error] Ollama call failed: {e}", file=sys.stderr)
        return None


def parse_json_response(response):
    """Extract JSON from LLM response, handling markdown wrappers."""
    if not response:
        return None
    cleaned = response.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
    if cleaned.startswith("json"):
        cleaned = cleaned[4:].strip()

    # Try direct parse first
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Try to find JSON array or object
    for start_char, end_char in [("[", "]"), ("{", "}")]:
        start = cleaned.find(start_char)
        end = cleaned.rfind(end_char) + 1
        if start >= 0 and end > start:
            try:
                return json.loads(cleaned[start:end])
            except json.JSONDecodeError:
                continue
    return None


def gather_context(db):
    """Gather all pipeline intelligence for the forecasting prompt."""
    # Approved signals
    approved = db.execute("""
        SELECT s.extracted_pain, s.industry, s.subreddit, t.composite_score,
               t.existing_solutions, t.monetization_model, t.target_audience
        FROM harvest_signals s
        JOIN harvest_triage t ON t.signal_id = s.id
        WHERE t.verdict = 'approved'
        ORDER BY t.composite_score DESC
    """).fetchall()

    # Rejected signals with reasons (what patterns DON'T work)
    rejected = db.execute("""
        SELECT s.extracted_pain, s.industry, t.verdict_reason, t.human_notes
        FROM harvest_signals s
        JOIN harvest_triage t ON t.signal_id = s.id
        WHERE t.verdict = 'rejected' AND t.human_reviewed = 1
        LIMIT 10
    """).fetchall()

    # Industry stats
    industries = db.execute("""
        SELECT * FROM harvest_industry_stats ORDER BY signals_found DESC
    """).fetchall()

    # High-scoring but not-approved (interesting but flawed)
    near_misses = db.execute("""
        SELECT s.extracted_pain, s.industry, t.composite_score, t.human_notes
        FROM harvest_signals s
        JOIN harvest_triage t ON t.signal_id = s.id
        WHERE t.verdict = 'rejected' AND t.composite_score >= 5.0 AND t.human_reviewed = 1
    """).fetchall()

    # Existing forecasts (to avoid duplicates)
    existing = db.execute("""
        SELECT title, description FROM harvest_forecasts WHERE status != 'killed'
    """).fetchall()

    return {
        "approved": [dict(r) for r in approved],
        "rejected": [dict(r) for r in rejected],
        "industries": [dict(r) for r in industries],
        "near_misses": [dict(r) for r in near_misses],
        "existing_forecasts": [dict(r) for r in existing],
    }


def build_forecast_prompt(context, mode, count):
    """Build the forecasting prompt based on mode and context."""

    approved_text = "\n".join([
        f"  - [{r.get('industry', '?')}] {r.get('extracted_pain', 'N/A')} "
        f"(score: {r.get('composite_score', '?')}, audience: {r.get('target_audience', '?')})"
        for r in context["approved"]
    ]) or "  (no approved signals yet)"

    rejected_text = "\n".join([
        f"  - [{r.get('industry', '?')}] {r.get('extracted_pain', 'N/A')} — rejected: {r.get('human_notes', r.get('verdict_reason', '?'))}"
        for r in context["rejected"][:5]
    ]) or "  (no rejected signals yet)"

    industry_text = "\n".join([
        f"  - {r.get('industry', '?')}: {r.get('signals_found', 0)} signals, {r.get('signals_approved', 0)} approved"
        for r in context["industries"]
    ]) or "  (no industry data yet)"

    near_miss_text = "\n".join([
        f"  - [{r.get('industry', '?')}] {r.get('extracted_pain', 'N/A')} (score: {r.get('composite_score', '?')}) — {r.get('human_notes', 'no notes')}"
        for r in context["near_misses"]
    ]) or "  (none)"

    existing_text = "\n".join([
        f"  - {r.get('title', '?')}"
        for r in context["existing_forecasts"]
    ]) or "  (none yet)"

    mode_instructions = {
        "pattern": """Focus on META-TRENDS. What patterns connect the approved signals? What larger movement or shift do they represent? Generate product ideas that ride the wave of where these patterns are heading, not just where they are now.""",

        "silence": """Focus on SILENCE GAPS. Look at what industries and problem types appear in rejected signals but NOT in approved ones. Look at what's conspicuously ABSENT — problems that logically should exist given the approved patterns but that nobody is talking about. Sometimes the lack of noise IS the signal. What are people NOT complaining about that they should be?""",

        "adjacent": """Focus on ADJACENT PROBLEMS. For each approved signal, what problem exists right next to it that nobody's mentioning? If someone needs X, they almost certainly also need Y — but nobody's asking for Y yet. Find those Y problems.""",

        "upstream": """Focus on ROOT CAUSES. The approved signals are symptoms. What are the upstream causes? If you could solve the cause instead of the symptom, you'd eliminate entire categories of complaints. What systemic tools would prevent these problems from existing?""",

        "automate": """Focus on AUTOMATION OPPORTUNITIES. What manual, repetitive, time-consuming processes exist in the industries where signals are appearing? Find things that humans are doing today that could be fully automated with current AI/software capabilities. Prioritize solutions that can run with ZERO ongoing human involvement after initial setup.""",

        "creative": """Use ALL perspectives — pattern synthesis, silence gaps, adjacent problems, root causes, and automation opportunities. Be creative and surprising. The best ideas will be non-obvious combinations that nobody is asking for but that, once described, seem inevitable.""",
    }

    return f"""You are a product forecaster analyzing market intelligence data. Your job is to generate {count} novel product ideas that are NOT being directly signalled by users but that emerge from the patterns in the data.

CRITICAL CONSTRAINT: Every idea must be scored on AUTONOMY — how hands-off can this product be after initial setup? We are looking for products that:
- Require minimal or zero ongoing human support
- Generate recurring revenue passively
- Can scale without proportionally scaling human effort
- Are buildable by a small team (1-2 people with AI agent assistance)

IMPORTANT: Solutions are NOT limited to software. Consider:
- PHYSICAL PRODUCTS via automated supply chains (e.g., print-on-demand, white-label, dropship, wholesale + custom branding)
- SERVICE ARBITRAGE — chaining existing services/APIs together into a new offering (e.g., nontoxic clothing wholesaler + custom printing API = automated eco-fashion brand)
- HYBRID solutions — software frontend + physical fulfillment backend
- DATA PRODUCTS — curated datasets, reports, alerts that update automatically
- BOTS and AGENTS — autonomous services that perform ongoing work for subscribers
The best ideas often chain 2-3 existing services together via APIs in a way nobody has assembled yet. The orchestration IS the product.

{mode_instructions.get(mode, mode_instructions['creative'])}

## Pipeline Intelligence

### Approved Signals (what's working):
{approved_text}

### Rejected Signals (what's NOT working and why):
{rejected_text}

### Industry Distribution:
{industry_text}

### Near-Misses (high-scoring but rejected — interesting but flawed):
{near_miss_text}

### Already Forecasted (don't duplicate):
{existing_text}

## Output Format

Return ONLY a JSON array of {count} product ideas. Each object must have:
{{
  "title": "Short product name",
  "description": "2-3 sentence product description",
  "origin_type": "pattern_synthesis|silence_gap|adjacent_problem|upstream_cause|automation_opportunity",
  "origin_reasoning": "Why this idea emerged from the data — what pattern, gap, or insight led here",
  "autonomy_breakdown": {{
    "setup": <1-10, 10=trivial setup>,
    "operation": <1-10, 10=fully autonomous>,
    "support": <1-10, 10=zero support needed>,
    "maintenance": <1-10, 10=zero maintenance>
  }},
  "revenue_model": "recurring_passive|recurring_active|one_time|usage_based",
  "recurring_potential": <1-10>,
  "market_size_score": <1-10>,
  "monetization_score": <1-10>,
  "build_complexity_score": <1-10, 10=trivial>,
  "existing_solutions_score": <1-10, 10=unserved>,
  "soy_leaf_fit_score": <1-10>,
  "industry": "industry name",
  "build_type": "soy_leaf|standalone_saas|api_service|chrome_extension|bot|marketplace|data_product|fiverr_service|physical_product|service_chain|hybrid_digital_physical|automated_agency",
  "target_audience": "who pays",
  "estimated_build_days": <number>,
  "estimated_mrr_low": <dollars>,
  "estimated_mrr_high": <dollars>
}}

JSON:"""


def compute_autonomy_score(breakdown):
    """Weighted autonomy score — operation and support matter most."""
    if not breakdown:
        return None
    weights = {"setup": 0.15, "operation": 0.35, "support": 0.30, "maintenance": 0.20}
    total = sum(breakdown.get(k, 5) * w for k, w in weights.items())
    return round(total, 1)


def compute_composite(idea):
    """Same composite as triage for comparability."""
    weights = {
        "market_size_score": 0.20,
        "monetization_score": 0.25,
        "build_complexity_score": 0.10,
        "existing_solutions_score": 0.15,
        "soy_leaf_fit_score": 0.05,
    }
    # Add autonomy and recurring as factors (this is the forecaster's edge)
    autonomy = compute_autonomy_score(idea.get("autonomy_breakdown", {}))
    recurring = idea.get("recurring_potential", 5)

    base = sum(idea.get(k, 5) * w for k, w in weights.items())
    # Autonomy and recurring add 25% of the score
    auto_bonus = (autonomy or 5) * 0.15 + recurring * 0.10
    return round(base + auto_bonus, 2)


def cmd_generate(args):
    """Generate product forecasts."""
    db = get_db()
    context = gather_context(db)
    mode = args.mode or "creative"
    count = args.count or 5

    print(f"Forecasting {count} product ideas (mode: {mode})...")
    print(f"Context: {len(context['approved'])} approved signals, "
          f"{len(context['industries'])} industries tracked")
    print()

    prompt = build_forecast_prompt(context, mode, count)
    response = ollama_generate(FORECAST_MODEL, prompt)

    if not response:
        print("LLM forecasting failed.")
        return

    ideas = parse_json_response(response)
    if not ideas or not isinstance(ideas, list):
        print(f"Failed to parse forecast response.")
        print(f"Raw: {response[:500]}")
        return

    stored = 0
    for idea in ideas:
        if not isinstance(idea, dict) or not idea.get("title"):
            continue

        autonomy = compute_autonomy_score(idea.get("autonomy_breakdown", {}))
        composite = compute_composite(idea)

        db.execute("""
            INSERT INTO harvest_forecasts
                (title, description, origin_type, origin_reasoning,
                 autonomy_score, autonomy_breakdown, revenue_model, recurring_potential,
                 market_size_score, monetization_score, build_complexity_score,
                 existing_solutions_score, soy_leaf_fit_score, composite_score,
                 industry, build_type, target_audience,
                 estimated_build_days, estimated_mrr_low, estimated_mrr_high)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            idea.get("title"),
            idea.get("description", ""),
            idea.get("origin_type", "creative"),
            idea.get("origin_reasoning", ""),
            autonomy,
            json.dumps(idea.get("autonomy_breakdown", {})),
            idea.get("revenue_model", "recurring_passive"),
            idea.get("recurring_potential", 5),
            idea.get("market_size_score"),
            idea.get("monetization_score"),
            idea.get("build_complexity_score"),
            idea.get("existing_solutions_score"),
            idea.get("soy_leaf_fit_score"),
            composite,
            idea.get("industry"),
            idea.get("build_type"),
            idea.get("target_audience"),
            idea.get("estimated_build_days"),
            idea.get("estimated_mrr_low"),
            idea.get("estimated_mrr_high"),
        ))
        stored += 1

        auto_bd = idea.get("autonomy_breakdown", {})
        print(f"  [{idea.get('origin_type', '?')}] {idea['title']}")
        print(f"    {idea.get('description', '')[:200]}")
        print(f"    Autonomy: {autonomy}/10 (setup:{auto_bd.get('setup','?')} op:{auto_bd.get('operation','?')} "
              f"support:{auto_bd.get('support','?')} maint:{auto_bd.get('maintenance','?')})")
        print(f"    Composite: {composite} | Revenue: {idea.get('revenue_model', '?')} | "
              f"Recurring: {idea.get('recurring_potential', '?')}/10")
        print(f"    Build: ~{idea.get('estimated_build_days', '?')} days | "
              f"MRR: ${idea.get('estimated_mrr_low', '?')}-${idea.get('estimated_mrr_high', '?')}")
        print(f"    Type: {idea.get('build_type', '?')} | Audience: {idea.get('target_audience', '?')}")
        print()

    db.commit()

    # Log to activity
    db.execute("""
        INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
        VALUES ('forecast', 0, 'forecasts_generated', ?, datetime('now'))
    """, (json.dumps({"mode": mode, "count": stored}),))
    db.commit()

    print(f"{'='*60}")
    print(f"Generated {stored} forecasts. Use 'list' to review, 'approve <id>' to greenlight.")
    db.close()


def cmd_list(args):
    """List forecasts."""
    db = get_db()

    query = "SELECT * FROM harvest_forecasts WHERE 1=1"
    params = []

    if args.status:
        query += " AND status = ?"
        params.append(args.status)
    if args.min_autonomy:
        query += " AND autonomy_score >= ?"
        params.append(args.min_autonomy)

    query += " ORDER BY composite_score DESC LIMIT ?"
    params.append(args.limit or 20)

    rows = db.execute(query, params).fetchall()

    if not rows:
        print("No forecasts found. Run 'generate' first.")
        return

    print(f"Product Forecasts (sorted by composite score)")
    print("=" * 65)

    for row in rows:
        auto_bd = json.loads(row["autonomy_breakdown"]) if row["autonomy_breakdown"] else {}
        print(f"\n{'─'*65}")
        print(f"#{row['id']} [{row['status'].upper()}] {row['title']}")
        print(f"  {row['description'][:250]}")
        print(f"  Origin: {row['origin_type']} — {(row['origin_reasoning'] or '')[:150]}")
        print(f"  Autonomy: {row['autonomy_score']}/10 | Composite: {row['composite_score']}/10")
        print(f"  Revenue: {row['revenue_model']} | Recurring: {row['recurring_potential']}/10")
        print(f"  Build: ~{row['estimated_build_days']} days | "
              f"MRR: ${row['estimated_mrr_low'] or 0:.0f}-${row['estimated_mrr_high'] or 0:.0f}")
        print(f"  Type: {row['build_type']} | Industry: {row['industry']}")
        print(f"  → approve {row['id']} | kill {row['id']}")

    print(f"\n{'─'*65}")
    print(f"Showing {len(rows)} forecasts")
    db.close()


def cmd_approve(args):
    db = get_db()
    db.execute("""
        UPDATE harvest_forecasts SET status = 'approved', human_notes = ?, updated_at = datetime('now')
        WHERE id = ?
    """, (args.notes or "Approved for build", args.forecast_id))
    db.execute("""
        INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
        VALUES ('forecast', ?, 'forecast_approved', 'Forecast approved for build pipeline', datetime('now'))
    """, (args.forecast_id,))
    db.commit()
    print(f"Forecast #{args.forecast_id} approved.")
    db.close()


def cmd_kill(args):
    db = get_db()
    db.execute("""
        UPDATE harvest_forecasts SET status = 'killed', human_notes = ?, updated_at = datetime('now')
        WHERE id = ?
    """, (args.reason or "Killed", args.forecast_id))
    db.commit()
    print(f"Forecast #{args.forecast_id} killed.")
    db.close()


def main():
    parser = argparse.ArgumentParser(description="Signal Forecast — creative product ideation")
    subparsers = parser.add_subparsers(dest="command")

    p_gen = subparsers.add_parser("generate", help="Generate product forecasts")
    p_gen.add_argument("--mode", choices=["pattern", "silence", "adjacent", "upstream", "automate", "creative"],
                       default="creative")
    p_gen.add_argument("--count", type=int, default=5)

    p_list = subparsers.add_parser("list", help="List forecasts")
    p_list.add_argument("--status", choices=["idea", "evaluated", "approved", "building", "shipped", "killed"])
    p_list.add_argument("--min-autonomy", type=int)
    p_list.add_argument("--limit", type=int, default=20)

    p_approve = subparsers.add_parser("approve", help="Approve a forecast")
    p_approve.add_argument("forecast_id", type=int)
    p_approve.add_argument("--notes")

    p_kill = subparsers.add_parser("kill", help="Kill a forecast")
    p_kill.add_argument("forecast_id", type=int)
    p_kill.add_argument("reason", nargs="?", default="Killed")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    {"generate": cmd_generate, "list": cmd_list, "approve": cmd_approve, "kill": cmd_kill}[args.command](args)


if __name__ == "__main__":
    main()
