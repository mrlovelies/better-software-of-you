#!/usr/bin/env python3
"""
Competitive Intelligence — Harvest dissatisfaction with existing products.

Different from signal_harvester: this targets NAMED products/services being
trashed, complained about, or abandoned. The thesis: if people hate an existing
thing, build the same thing but better.

Applies to software, physical products, and services.

Usage:
  python3 competitive_intel.py harvest [--category=...] [--limit=25]
  python3 competitive_intel.py harvest --product="Notion"
  python3 competitive_intel.py analyze [--limit=20]
  python3 competitive_intel.py targets
  python3 competitive_intel.py opportunity <product_name>
  python3 competitive_intel.py review
  python3 competitive_intel.py approve <signal_id>
  python3 competitive_intel.py reject <signal_id> [reason]
"""

import sys
import os
import json
import sqlite3
import re
import time
import argparse
from urllib.request import Request, urlopen
from urllib.parse import quote_plus

PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(PLUGIN_ROOT, "data", "soy.db")

try:
    from content_sanitizer import sanitize_signal
    HAS_SANITIZER = True
except ImportError:
    HAS_SANITIZER = False
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://100.91.234.67:11434")
OLLAMA_HOST_14B = os.environ.get("OLLAMA_HOST_14B", "http://100.74.238.16:11434")
REDDIT_SEARCH_URL = "https://www.reddit.com/search.json"
USER_AGENT = "SoY-CompetitiveIntel/1.0 (personal research tool)"

ANALYSIS_MODEL = "qwen2.5:14b"
FILTER_MODEL = "mistral:7b"

# Dissatisfaction patterns — people trashing existing products
COMPLAINT_PATTERNS = [
    r"\b(?:i |we |finally )?(?:switched|moved|migrated) (?:away )?from\b",
    r"\b(?:i'?m |we'?re )?(?:leaving|ditching|dropping|abandoning|quitting)\b.{0,30}\b(?:because|since|after)\b",
    r"\b(?:used to (?:love|use|like)|was (?:a fan|loyal))\b.{0,40}\b(?:but now|until|however)\b",
    r"\b(?:worst|terrible|horrible|awful|garbage|trash|unusable|broken)\b.{0,30}\b(?:app|tool|service|product|software|platform)\b",
    r"\b(?:why (?:does|is)|how (?:can|does))\b.{0,30}\b(?:so (?:bad|broken|slow|expensive|buggy))\b",
    r"\b(?:overpriced|ripoff|rip-off|scam|money grab|cash grab)\b",
    r"\b(?:looking for|need|want) (?:an? )?alternative(?:s)? to\b",
    r"\b(?:better|cheaper|simpler|faster) (?:alternative|option|replacement) (?:to|for|than)\b",
    r"\bif only .{0,30} (?:could|would|had)\b",
    r"\b(?:missing|lacks|doesn'?t have|no support for|still no|where is the)\b.{0,30}\b(?:feature|option|ability|function)\b",
    r"\b(?:i'?d pay|shut up and take my money|take my money)\b.{0,30}\b(?:if|for)\b",
    r"\b(?:cancelled|unsubscribed|deleted) my .{0,20}(?:account|subscription|membership)\b",
    r"\b(?:enshittification|enshittified|getting worse|going downhill|used to be good)\b",
]

COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in COMPLAINT_PATTERNS]

# Search queries that surface product dissatisfaction
COMPLAINT_QUERIES = [
    "switched away from because",
    "alternative to looking for",
    "worst app terrible experience",
    "cancelled subscription because",
    "used to love but now",
    "missing feature why doesn't",
    "overpriced better alternative",
    "enshittification getting worse",
    "finally ditching moving to",
    "broken unusable frustrating app",
]

# Targeted queries for specific product categories
CATEGORY_QUERIES = {
    "project_management": ["alternative to Notion", "switched from Asana because", "Trello getting worse"],
    "accounting": ["QuickBooks alternative", "switched from FreshBooks", "Xero missing features"],
    "design": ["Canva alternative for professionals", "Adobe too expensive alternative", "Figma complaints"],
    "ecommerce": ["Shopify alternative cheaper", "Etsy seller complaints", "Amazon seller frustrated"],
    "fashion": ["fast fashion alternative sustainable", "Shein quality terrible", "wish clothing better quality"],
    "food_delivery": ["DoorDash terrible", "UberEats complaints", "food delivery alternative"],
    "fitness": ["Peloton alternative cheaper", "fitness app subscription too expensive"],
    "crm": ["Salesforce alternative small business", "HubSpot too expensive"],
    "hosting": ["GoDaddy terrible alternative", "Squarespace limitations", "Wix frustrating"],
    "communication": ["Slack alternative", "Teams terrible", "Discord for business"],
}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def ollama_generate(model, prompt, temperature=0.1):
    host = OLLAMA_HOST_14B if "14b" in model else OLLAMA_HOST
    url = f"{host}/api/generate"
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": 2048},
    }).encode()
    req = Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read().decode())
            return data.get("response", "").strip()
    except Exception as e:
        print(f"  [error] Ollama call failed ({model}): {e}", file=sys.stderr)
        return None


def reddit_fetch(url, params=None):
    if params:
        query_string = "&".join(f"{k}={quote_plus(str(v))}" for k, v in params.items())
        url = f"{url}?{query_string}"
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            time.sleep(1.5)
            return data
    except Exception as e:
        print(f"  [warn] Reddit fetch failed: {e}", file=sys.stderr)
        return None


def matches_complaint_pattern(text):
    for pattern in COMPILED_PATTERNS:
        if pattern.search(text):
            return True
    return False


def filter_complaint(text):
    """Tier 1: Is this a real product complaint or noise?"""
    prompt = f"""Determine if this Reddit post is complaining about or expressing dissatisfaction with a SPECIFIC named product, service, app, brand, or company.

Answer ONLY "YES" or "NO" followed by the product name and a one-line reason.

YES means: The post names a specific product/service and expresses genuine dissatisfaction, missing features, or desire for an alternative.
NO means: This is general venting, not about a specific product, or is positive/neutral.

Post:
\"\"\"
{text[:2000]}
\"\"\"

Answer:"""

    response = ollama_generate(FILTER_MODEL, prompt)
    if not response:
        return None, None, "LLM failed"

    is_complaint = response.upper().startswith("YES")
    # Try to extract product name
    product = None
    if is_complaint:
        parts = response.split("\n")[0]
        # Look for product name after YES
        name_match = re.search(r'YES[:\s-]*([^.]+?)(?:\s*[-—]\s*|\.\s*|$)', parts)
        if name_match:
            product = name_match.group(1).strip()

    reason = response.split("\n")[0] if response else ""
    return is_complaint, product, reason


def analyze_complaint(text):
    """Tier 2: Deep analysis of the complaint."""
    prompt = f"""Analyze this product complaint and return ONLY valid JSON (no markdown):

{{
  "target_product": "Name of the product/service being complained about",
  "target_company": "Company that makes it (or null)",
  "target_category": "Product category (e.g. 'project management', 'fast fashion', 'food delivery')",
  "complaint_type": "missing_feature|poor_quality|overpriced|bad_ux|privacy_concern|reliability|poor_support|abandoned|bait_and_switch",
  "complaint_summary": "One sentence: what specifically is wrong",
  "missing_features": ["feature 1", "feature 2"],
  "sentiment_intensity": <1-10, how angry/frustrated>,
  "market_size_score": <1-10, how big is this product's market>,
  "switchability_score": <1-10, how easy for users to switch to an alternative>,
  "build_advantage_score": <1-10, how much better could a competitor make this>,
  "revenue_opportunity_score": <1-10, can we capture their paying users>
}}

Post:
\"\"\"
{text[:3000]}
\"\"\"

JSON:"""

    response = ollama_generate(ANALYSIS_MODEL, prompt, temperature=0.2)
    if not response:
        return None

    try:
        cleaned = response.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()

        start = cleaned.find("{")
        end = cleaned.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(cleaned[start:end])
    except json.JSONDecodeError:
        pass

    return None


def compute_composite(analysis):
    weights = {
        "market_size_score": 0.25,
        "switchability_score": 0.20,
        "build_advantage_score": 0.30,
        "revenue_opportunity_score": 0.25,
    }
    total = sum((analysis.get(k) or 5) * w for k, w in weights.items())
    return round(total, 2)


def update_target(db, analysis):
    """Update or create a competitive target entry."""
    product = (analysis.get("target_product") or "").strip()
    if not product:
        return

    existing = db.execute(
        "SELECT * FROM competitive_targets WHERE LOWER(product_name) = LOWER(?)",
        (product,)
    ).fetchone()

    if existing:
        # Update complaint count and recalculate
        db.execute("""
            UPDATE competitive_targets SET
                total_complaints = total_complaints + 1,
                updated_at = datetime('now')
            WHERE id = ?
        """, (existing["id"],))
    else:
        db.execute("""
            INSERT INTO competitive_targets
                (product_name, company, category, total_complaints)
            VALUES (?, ?, ?, 1)
        """, (product, analysis.get("target_company"), analysis.get("target_category")))


def cmd_harvest(args):
    """Harvest product complaints from Reddit."""
    db = get_db()

    queries = list(COMPLAINT_QUERIES)

    # Add category-specific queries
    if args.category and args.category in CATEGORY_QUERIES:
        queries = CATEGORY_QUERIES[args.category] + queries[:3]
        print(f"Category-focused harvest: {args.category}")

    # Add product-specific queries
    if args.product:
        queries = [
            f'"{args.product}" alternative',
            f'"{args.product}" switched because',
            f'"{args.product}" terrible frustrating',
            f'"{args.product}" missing feature',
            f'"{args.product}" overpriced',
        ] + queries[:3]
        print(f"Product-focused harvest: {args.product}")

    total_stored = 0
    total_skipped = 0

    print(f"Harvesting competitive intelligence with {len(queries)} queries...")

    for i, query in enumerate(queries):
        print(f"  [{i+1}/{len(queries)}] \"{query}\"")
        data = reddit_fetch(REDDIT_SEARCH_URL, {
            "q": query, "sort": "relevance", "t": args.time_filter or "month",
            "limit": min(args.limit or 25, 100),
        })

        if not data or "data" not in data:
            continue

        for child in data["data"].get("children", []):
            post = child.get("data", {})
            title = post.get("title", "")
            selftext = post.get("selftext", "")
            full_text = f"{title}\n\n{selftext}".strip()

            if len(full_text) < 50:
                continue

            if not matches_complaint_pattern(full_text):
                continue

            # Sanitize content (Ruflo AIDefence)
            clean_text = full_text[:5000]
            if HAS_SANITIZER:
                clean_text, _ = sanitize_signal(full_text[:5000])

            source_url = f"https://reddit.com{post.get('permalink', '')}"

            # Dedup
            existing = db.execute(
                "SELECT id FROM competitive_signals WHERE source_url = ?",
                (source_url,)
            ).fetchone()
            if existing:
                total_skipped += 1
                continue

            db.execute("""
                INSERT INTO competitive_signals
                    (source_url, source_author, platform, subreddit, raw_text,
                     upvotes, comment_count, harvested_at)
                VALUES (?, ?, 'reddit', ?, ?, ?, ?, datetime('now'))
            """, (
                source_url, post.get("author", "[deleted]"),
                post.get("subreddit", ""), clean_text,
                post.get("ups", 0), post.get("num_comments", 0),
            ))
            total_stored += 1

    db.commit()

    print(f"\n{'='*50}")
    print(f"Competitive harvest complete:")
    print(f"  New stored:     {total_stored}")
    print(f"  Dupes skipped:  {total_skipped}")
    print(f"  Total in DB:    {db.execute('SELECT COUNT(*) FROM competitive_signals').fetchone()[0]}")

    db.execute("""
        INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
        VALUES ('competitive', 0, 'competitive_harvest',
            json_object('stored', ?, 'skipped', ?, 'product', ?, 'category', ?),
            datetime('now'))
    """, (total_stored, total_skipped, args.product, args.category))
    db.commit()
    db.close()


def cmd_analyze(args):
    """Filter and analyze unprocessed competitive signals."""
    db = get_db()

    # Get unanalyzed signals
    signals = db.execute("""
        SELECT * FROM competitive_signals
        WHERE complaint_summary IS NULL
        ORDER BY upvotes DESC
        LIMIT ?
    """, (args.limit or 20,)).fetchall()

    if not signals:
        print("No unanalyzed competitive signals. Run 'harvest' first.")
        return

    print(f"Analyzing {len(signals)} competitive signals...")
    print(f"  Tier 1: {FILTER_MODEL} (noise filter)")
    print(f"  Tier 2: {ANALYSIS_MODEL} (deep analysis)")
    print(f"{'='*60}")

    passed = 0
    filtered = 0

    for sig in signals:
        text_preview = sig["raw_text"][:80].replace("\n", " ")
        print(f"\n  #{sig['id']} r/{sig['subreddit']} ({sig['upvotes']}↑): {text_preview}...")

        # Tier 1: Is this a real product complaint?
        is_complaint, product_hint, reason = filter_complaint(sig["raw_text"])

        if is_complaint is None:
            print(f"    [skip] Filter error")
            continue

        if not is_complaint:
            db.execute("""
                UPDATE competitive_signals SET
                    complaint_summary = ?, verdict = 'rejected',
                    updated_at = datetime('now')
                WHERE id = ?
            """, (f"T1 rejected: {reason}", sig["id"]))
            filtered += 1
            print(f"    [NOISE] {reason}")
            continue

        # Tier 2: Deep analysis
        analysis = analyze_complaint(sig["raw_text"])

        if not analysis:
            print(f"    [skip] Analysis failed")
            continue

        composite = compute_composite(analysis)
        missing_features = json.dumps(analysis.get("missing_features", []))

        db.execute("""
            UPDATE competitive_signals SET
                target_product = ?,
                target_company = ?,
                target_category = ?,
                complaint_type = ?,
                complaint_summary = ?,
                missing_features = ?,
                sentiment_intensity = ?,
                market_size_score = ?,
                switchability_score = ?,
                build_advantage_score = ?,
                revenue_opportunity_score = ?,
                composite_score = ?,
                updated_at = datetime('now')
            WHERE id = ?
        """, (
            analysis.get("target_product", product_hint),
            analysis.get("target_company"),
            analysis.get("target_category"),
            analysis.get("complaint_type"),
            analysis.get("complaint_summary"),
            missing_features,
            analysis.get("sentiment_intensity"),
            analysis.get("market_size_score"),
            analysis.get("switchability_score"),
            analysis.get("build_advantage_score"),
            analysis.get("revenue_opportunity_score"),
            composite,
            sig["id"],
        ))

        update_target(db, analysis)
        passed += 1

        print(f"    [HIT] {analysis.get('target_product', '?')} — {analysis.get('complaint_type', '?')}")
        print(f"    {analysis.get('complaint_summary', '?')}")
        print(f"    Composite: {composite} | Switch: {analysis.get('switchability_score', '?')}/10 | "
              f"Advantage: {analysis.get('build_advantage_score', '?')}/10")
        if analysis.get("missing_features"):
            print(f"    Missing: {', '.join(analysis['missing_features'][:3])}")

    db.commit()

    print(f"\n{'='*60}")
    print(f"Analysis complete: {passed} opportunities, {filtered} noise")
    db.close()


def cmd_targets(args):
    """Show competitive targets ranked by opportunity."""
    db = get_db()

    # Recalculate target aggregates
    targets = db.execute("""
        SELECT
            ct.*,
            COUNT(cs.id) as signal_count,
            AVG(cs.sentiment_intensity) as avg_sentiment,
            AVG(cs.composite_score) as avg_composite,
            GROUP_CONCAT(DISTINCT cs.complaint_type) as complaint_types
        FROM competitive_targets ct
        LEFT JOIN competitive_signals cs ON LOWER(cs.target_product) = LOWER(ct.product_name)
            AND cs.verdict != 'rejected'
        GROUP BY ct.id
        ORDER BY avg_composite DESC NULLS LAST
    """).fetchall()

    if not targets:
        print("No competitive targets tracked yet. Run 'harvest' then 'analyze'.")
        return

    print("Competitive Targets (ranked by opportunity)")
    print("=" * 65)

    for t in targets:
        print(f"\n{'─'*65}")
        print(f"  {t['product_name']} ({t['company'] or '?'}) — {t['category'] or '?'}")
        print(f"  Complaints: {t['signal_count']} | Avg sentiment: {t['avg_sentiment'] or 0:.1f}/10 | "
              f"Avg composite: {t['avg_composite'] or 0:.1f}/10")
        if t['complaint_types']:
            print(f"  Types: {t['complaint_types']}")
        print(f"  Status: {t['status']}")

    print(f"\n{'─'*65}")
    print(f"Tracking {len(targets)} products")
    db.close()


def cmd_opportunity(args):
    """Deep dive on a specific product's complaints."""
    db = get_db()
    product = args.product_name

    signals = db.execute("""
        SELECT * FROM competitive_signals
        WHERE LOWER(target_product) LIKE LOWER(?)
        AND verdict != 'rejected'
        ORDER BY composite_score DESC
    """, (f"%{product}%",)).fetchall()

    if not signals:
        print(f"No complaints found for '{product}'. Try harvesting: "
              f"python3 competitive_intel.py harvest --product=\"{product}\"")
        return

    print(f"Opportunity Analysis: {product}")
    print("=" * 60)

    # Aggregate missing features
    all_features = []
    complaint_types = {}
    for s in signals:
        if s["missing_features"]:
            try:
                all_features.extend(json.loads(s["missing_features"]))
            except json.JSONDecodeError:
                pass
        ct = s["complaint_type"] or "unknown"
        complaint_types[ct] = complaint_types.get(ct, 0) + 1

    # Deduplicate features roughly
    feature_counts = {}
    for f in all_features:
        key = f.lower().strip()
        feature_counts[key] = feature_counts.get(key, 0) + 1

    print(f"\nTotal complaints: {len(signals)}")

    if complaint_types:
        print(f"\nComplaint types:")
        for ct, count in sorted(complaint_types.items(), key=lambda x: -x[1]):
            print(f"  {ct}: {count}")

    if feature_counts:
        print(f"\nMost requested missing features:")
        for feat, count in sorted(feature_counts.items(), key=lambda x: -x[1])[:10]:
            print(f"  [{count}x] {feat}")

    print(f"\nTop complaints:")
    for s in signals[:5]:
        print(f"\n  #{s['id']} ({s['upvotes']}↑) — {s['complaint_type']}")
        print(f"  {s['complaint_summary']}")
        print(f"  Composite: {s['composite_score']} | Switch: {s['switchability_score']}/10 | "
              f"Advantage: {s['build_advantage_score']}/10")

    print(f"\n{'='*60}")
    print(f"To build a competitor: identify the top missing features, build those first.")
    db.close()


def cmd_review(args):
    """Show competitive signals awaiting human review."""
    db = get_db()

    rows = db.execute("""
        SELECT * FROM competitive_signals
        WHERE complaint_summary IS NOT NULL
        AND verdict = 'pending' AND human_reviewed = 0
        ORDER BY composite_score DESC
        LIMIT ?
    """, (args.limit if hasattr(args, 'limit') else 20,)).fetchall()

    if not rows:
        print("No signals awaiting review. Run 'harvest' then 'analyze'.")
        return

    print("Competitive Signals — Human Review Queue")
    print("=" * 65)

    for r in rows:
        features = json.loads(r["missing_features"]) if r["missing_features"] else []
        print(f"\n{'─'*65}")
        print(f"#{r['id']} | {r['target_product'] or '?'} ({r['target_category'] or '?'}) | "
              f"Composite: {r['composite_score']}/10")
        print(f"  Type: {r['complaint_type']} | Sentiment: {r['sentiment_intensity']}/10 | "
              f"{r['upvotes']}↑ {r['comment_count']}💬")
        print(f"  {r['complaint_summary']}")
        if features:
            print(f"  Missing: {', '.join(features[:4])}")
        print(f"  Switch: {r['switchability_score']}/10 | Advantage: {r['build_advantage_score']}/10 | "
              f"Revenue: {r['revenue_opportunity_score']}/10")
        text = r["raw_text"][:200].replace("\n", " ")
        print(f"  Text: {text}...")
        print(f"  → approve {r['id']} | reject {r['id']}")

    db.close()


def cmd_approve(args):
    db = get_db()
    db.execute("""
        UPDATE competitive_signals SET verdict = 'opportunity', human_reviewed = 1,
            human_notes = ?, updated_at = datetime('now')
        WHERE id = ?
    """, (args.notes or "Approved as opportunity", args.signal_id))
    db.commit()
    print(f"Signal #{args.signal_id} marked as opportunity.")
    db.close()


def cmd_reject(args):
    db = get_db()
    db.execute("""
        UPDATE competitive_signals SET verdict = 'rejected', human_reviewed = 1,
            human_notes = ?, updated_at = datetime('now')
        WHERE id = ?
    """, (args.reason or "Rejected", args.signal_id))
    db.commit()
    print(f"Signal #{args.signal_id} rejected.")
    db.close()


def main():
    parser = argparse.ArgumentParser(description="Competitive Intelligence — harvest product dissatisfaction")
    subparsers = parser.add_subparsers(dest="command")

    p_harvest = subparsers.add_parser("harvest", help="Harvest product complaints")
    p_harvest.add_argument("--product", help="Target a specific product")
    p_harvest.add_argument("--category", help="Target a product category",
                          choices=list(CATEGORY_QUERIES.keys()))
    p_harvest.add_argument("--limit", type=int, default=25)
    p_harvest.add_argument("--time-filter", default="month",
                          choices=["hour", "day", "week", "month", "year", "all"])

    p_analyze = subparsers.add_parser("analyze", help="Filter and analyze unprocessed signals")
    p_analyze.add_argument("--limit", type=int, default=20)

    subparsers.add_parser("targets", help="Show tracked competitive targets")

    p_opp = subparsers.add_parser("opportunity", help="Deep dive on a product's complaints")
    p_opp.add_argument("product_name")

    p_review = subparsers.add_parser("review", help="Human review queue")
    p_review.add_argument("--limit", type=int, default=20)

    p_approve = subparsers.add_parser("approve", help="Mark as opportunity")
    p_approve.add_argument("signal_id", type=int)
    p_approve.add_argument("--notes")

    p_reject = subparsers.add_parser("reject", help="Reject signal")
    p_reject.add_argument("signal_id", type=int)
    p_reject.add_argument("reason", nargs="?", default="Rejected")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    cmds = {
        "harvest": cmd_harvest, "analyze": cmd_analyze, "targets": cmd_targets,
        "opportunity": cmd_opportunity, "review": cmd_review,
        "approve": cmd_approve, "reject": cmd_reject,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()
