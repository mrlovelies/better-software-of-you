#!/usr/bin/env python3
"""
Monetization Strategy Generator — produces concrete monetization plans
for forecasts and approved signals.

Not "recurring_passive" — actual channel-by-channel breakdown:
who pays, how much, how the money moves, what the margins are.

Usage:
  python3 monetization_gen.py forecasts [--limit=20]
  python3 monetization_gen.py signals [--limit=20]
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
MODEL = "qwen2.5:14b"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def ollama_generate(prompt):
    url = f"{OLLAMA_HOST_14B}/api/generate"
    payload = json.dumps({
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 1024},
    }).encode()
    req = Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read().decode())
            return data.get("response", "").strip()
    except Exception as e:
        print(f"  [error] Ollama failed: {e}", file=sys.stderr)
        return None


def generate_strategy(title, description, revenue_model, build_type, target_audience, industry, mrr_low, mrr_high):
    prompt = f"""You are a monetization strategist. Given a product concept, produce a CONCRETE monetization plan.

Do NOT just say "subscription" or "freemium." Spell out the specific revenue channels, who pays, how much, how the money moves, what the margins look like, and what the path to the MRR target is.

Product: {title}
Description: {description}
Revenue Model Type: {revenue_model}
Build Type: {build_type}
Target Audience: {target_audience}
Industry: {industry}
MRR Target: ${mrr_low:.0f}-${mrr_high:.0f}/month

Return a JSON object with these fields:
{{
  "channels": [
    {{
      "name": "channel name (e.g. 'Venue partnerships', 'Premium tier', 'Affiliate commissions')",
      "description": "How this channel works — who pays, for what, how",
      "pricing": "Specific pricing (e.g. '$50-200/mo per venue', '$3/mo per user')",
      "estimated_monthly": "$X-Y range",
      "margin": "estimated margin percentage",
      "effort_to_launch": "low/medium/high"
    }}
  ],
  "path_to_mrr": "2-3 sentences: the realistic path from $0 to the MRR target. What needs to happen, in what order.",
  "key_assumption": "The single biggest assumption that must be true for this to work",
  "biggest_risk": "What's most likely to kill the revenue"
}}

JSON:"""

    return ollama_generate(prompt)


def parse_json_response(response):
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
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(cleaned[start:end])
            except json.JSONDecodeError:
                pass
    return None


def cmd_forecasts(args):
    db = get_db()

    forecasts = db.execute("""
        SELECT * FROM harvest_forecasts
        WHERE monetization_strategy IS NULL AND status <> 'killed'
        ORDER BY composite_score DESC
        LIMIT ?
    """, (args.limit,)).fetchall()

    if not forecasts:
        print("All forecasts already have monetization strategies.")
        return

    print(f"Generating monetization strategies for {len(forecasts)} forecasts...")

    for f in forecasts:
        print(f"\n  {f['title']}...")
        response = generate_strategy(
            f["title"], f["description"], f["revenue_model"], f["build_type"],
            f["target_audience"], f["industry"],
            f["estimated_mrr_low"] or 0, f["estimated_mrr_high"] or 0
        )

        strategy = parse_json_response(response)
        if strategy:
            db.execute("""
                UPDATE harvest_forecasts SET monetization_strategy = ?, updated_at = datetime('now')
                WHERE id = ?
            """, (json.dumps(strategy), f["id"]))
            db.commit()

            channels = strategy.get("channels", [])
            print(f"    {len(channels)} revenue channels identified:")
            for ch in channels:
                print(f"      - {ch.get('name', '?')}: {ch.get('pricing', '?')} ({ch.get('estimated_monthly', '?')})")
            if strategy.get("path_to_mrr"):
                print(f"    Path: {strategy['path_to_mrr'][:150]}")
        else:
            print(f"    [failed] Could not generate strategy")

    print(f"\nDone.")
    db.close()


def cmd_signals(args):
    db = get_db()

    # Get approved signals that don't have a monetization strategy in their triage notes
    signals = db.execute("""
        SELECT s.*, t.monetization_model, t.target_audience, t.build_estimate
        FROM harvest_signals s
        JOIN harvest_triage t ON t.signal_id = s.id
        WHERE t.verdict = 'approved'
        LIMIT ?
    """, (args.limit,)).fetchall()

    if not signals:
        print("No approved signals.")
        return

    print(f"Generating monetization strategies for {len(signals)} approved signals...")

    for s in signals:
        print(f"\n  Signal #{s['id']}: {s['extracted_pain'] or s['raw_text'][:80]}...")
        response = generate_strategy(
            f"Solution for: {s['extracted_pain'] or 'pain point'}",
            s["raw_text"][:500],
            s.get("monetization_model") or "freemium",
            "standalone_saas",
            s.get("target_audience") or "general",
            s["industry"] or "unknown",
            100, 1000
        )

        strategy = parse_json_response(response)
        if strategy:
            # Store as a note on the triage entry
            db.execute("""
                UPDATE harvest_triage SET
                    monetization_model = ?,
                    updated_at = datetime('now')
                WHERE signal_id = ?
            """, (json.dumps(strategy), s["id"]))
            db.commit()

            channels = strategy.get("channels", [])
            print(f"    {len(channels)} revenue channels")
        else:
            print(f"    [failed]")

    print(f"\nDone.")
    db.close()


def main():
    parser = argparse.ArgumentParser(description="Monetization Strategy Generator")
    subparsers = parser.add_subparsers(dest="command")

    p1 = subparsers.add_parser("forecasts", help="Generate for forecasts")
    p1.add_argument("--limit", type=int, default=20)

    p2 = subparsers.add_parser("signals", help="Generate for approved signals")
    p2.add_argument("--limit", type=int, default=20)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    {"forecasts": cmd_forecasts, "signals": cmd_signals}[args.command](args)


if __name__ == "__main__":
    main()
