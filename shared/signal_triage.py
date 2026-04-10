#!/usr/bin/env python3
"""
Signal Triage — Multi-LLM tiered evaluation of harvested signals.

Tier 1: Local LLM (Mistral 7B / Qwen 7B) — binary noise filter ($0)
Tier 2: Local LLM (Qwen 14B) — market viability scoring ($0)
Tier 3: Claude API — final synthesis + spec outline (pennies per signal)

Usage:
  python3 signal_triage.py filter [--limit=50] [--model=mistral:7b]
  python3 signal_triage.py score [--limit=20] [--model=qwen2.5:14b]
  python3 signal_triage.py synthesize [--limit=5]
  python3 signal_triage.py pipeline [--limit=50]    # runs all three tiers
  python3 signal_triage.py review                    # show signals awaiting human review
  python3 signal_triage.py approve <signal_id>       # approve a signal for build
  python3 signal_triage.py reject <signal_id> [reason]
"""

import sys
import os
import json
import sqlite3
import argparse
from urllib.request import Request, urlopen

PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(PLUGIN_ROOT, "data", "soy.db")

# Ollama endpoint — Razer (100.91.234.67) is always-on, Lucy (100.74.238.16) as overflow
# Set OLLAMA_HOST env var to override, or defaults to Razer via Tailscale
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://100.91.234.67:11434")
OLLAMA_HOST_14B = os.environ.get("OLLAMA_HOST_14B", "http://100.74.238.16:11434")

# Default models per tier
TIER1_MODEL = "mistral:7b"      # fast noise filter
TIER2_MODEL = "qwen2.5:14b"     # deeper analysis
# Tier 3 uses Claude API via anthropic SDK or direct API call

# Q-learning router (Ruflo extraction) for adaptive model selection
try:
    from q_router import QLearningRouter
    _q_router = QLearningRouter()
    HAS_ROUTER = True
except ImportError:
    _q_router = None
    HAS_ROUTER = False


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def ollama_generate(model, prompt, temperature=0.1):
    """Call Ollama's generate endpoint."""
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
        with urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read().decode())
            return data.get("response", "").strip()
    except Exception as e:
        print(f"  [error] Ollama call failed ({model}): {e}", file=sys.stderr)
        return None


def ollama_chat(model, messages, temperature=0.1):
    """Call Ollama's chat endpoint for structured conversations."""
    host = OLLAMA_HOST_14B if "14b" in model else OLLAMA_HOST
    url = f"{host}/api/chat"
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature},
    }).encode()

    req = Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read().decode())
            return data.get("message", {}).get("content", "").strip()
    except Exception as e:
        print(f"  [error] Ollama chat failed ({model}): {e}", file=sys.stderr)
        return None


def tier1_filter(signal_text, model=None):
    """
    Tier 1: Binary noise filter.
    Returns True if this is a genuine product/service pain point, False if noise.
    """
    model = model or TIER1_MODEL
    prompt = f"""You are a signal classifier for a demand-discovery pipeline. Determine if this Reddit post contains a GENUINE, ACTIONABLE product opportunity — something a small team could build and sell.

Answer ONLY "YES" or "NO" followed by a one-line reason.

YES means ALL of these are true:
- The post describes a specific, concrete problem (not general life frustration)
- A product, tool, service, or app could realistically solve it
- A small team (1-3 people) could feasibly build a solution
- People would plausibly pay for it

NO means ANY of these are true:
- It's a personal story, relationship drama, or emotional venting (even if it mentions "I wish")
- It's entertainment, fiction, sports discussion, political commentary, or community drama
- The poster is promoting their own product (not expressing a need)
- The problem is too vague ("life is hard") or too niche for anyone to pay for a solution
- The need is clearly already well-served by major existing products (e.g. "I need a calendar app")
- It's a content post (review, tutorial, guide, list) not a pain expression
- It's about a problem only the platform owner could fix (e.g. complaining about Reddit's own features)

Common false positives to watch for:
- r/BestofRedditorUpdates posts (relationship compilations — ALWAYS noise)
- "I wish" in the context of personal regret, not product needs
- Fashion/outfit posts where someone just wants validation
- Meta-Reddit or subreddit drama posts

Post:
\"\"\"
{signal_text[:2000]}
\"\"\"

Answer:"""

    response = ollama_generate(model, prompt)
    if not response:
        return None, "LLM call failed"

    is_signal = response.upper().startswith("YES")
    reason = response.split("\n")[0] if response else ""
    return is_signal, reason


def tier2_score(signal_text, model=None):
    """
    Tier 2: Market viability scoring.
    Returns a dict with scores and analysis.
    """
    model = model or TIER2_MODEL
    prompt = f"""You are a market analyst evaluating whether a pain point expressed online represents a viable product opportunity.

Analyze this post and return ONLY valid JSON (no markdown, no explanation) with these fields:

{{
  "extracted_pain": "One sentence describing the core unmet need",
  "industry": "The industry or domain (e.g. 'healthcare', 'freelancing', 'education', 'developer tools')",
  "market_size_score": <1-10, how many people likely have this problem>,
  "monetization_score": <1-10, likelihood people would pay for a solution>,
  "build_complexity_score": <1-10, 10=trivial to build, 1=extremely complex>,
  "existing_solutions_score": <1-10, 10=completely unserved, 1=many good solutions exist>,
  "soy_leaf_fit_score": <1-10, how well this fits as a personal data/productivity tool module>,
  "existing_solutions": "Known tools that partially address this (or 'None known')",
  "monetization_model": "Suggested pricing approach (subscription, one-time, freemium, etc.)",
  "build_estimate": "Rough scope: 'weekend project', '1-2 weeks', '1 month+', etc.",
  "target_audience": "Who would pay for this"
}}

Post:
\"\"\"
{signal_text[:3000]}
\"\"\"

JSON:"""

    response = ollama_generate(model, prompt, temperature=0.2)
    if not response:
        return None

    # Try to parse JSON from response
    try:
        # Handle cases where model wraps in markdown code blocks
        cleaned = response.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()

        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to extract JSON from mixed response
        start = response.find("{")
        end = response.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(response[start:end])
            except json.JSONDecodeError:
                pass
        print(f"  [warn] Failed to parse Tier 2 JSON response", file=sys.stderr)
        return None


TIER2_REQUIRED_FIELDS = [
    "market_size_score", "monetization_score", "build_complexity_score",
    "existing_solutions_score", "soy_leaf_fit_score",
]


def validate_tier2_output(scores):
    """Validate T2 JSON output: required fields present, scores in 1-10 range.
    Returns (is_valid, errors) tuple."""
    errors = []
    for field in TIER2_REQUIRED_FIELDS:
        val = scores.get(field)
        if val is None:
            errors.append(f"missing {field}")
        elif not isinstance(val, (int, float)):
            errors.append(f"{field} is not a number: {val}")
        elif val < 1 or val > 10:
            errors.append(f"{field} out of range (got {val}, expected 1-10)")
    return (len(errors) == 0, errors)


def compute_composite_score(scores):
    """Weighted composite score from individual dimensions."""
    weights = {
        "market_size_score": 0.25,
        "monetization_score": 0.30,
        "build_complexity_score": 0.15,
        "existing_solutions_score": 0.20,
        "soy_leaf_fit_score": 0.10,
    }
    total = 0
    for key, weight in weights.items():
        val = scores.get(key, 0)
        if isinstance(val, (int, float)):
            total += val * weight
    return round(total, 2)


def cmd_filter(args):
    """Tier 1: Run noise filter on unharvested signals."""
    db = get_db()
    model = args.model or TIER1_MODEL

    # Get signals that haven't been triaged yet
    signals = db.execute("""
        SELECT s.* FROM harvest_signals s
        LEFT JOIN harvest_triage t ON t.signal_id = s.id
        WHERE t.id IS NULL
        ORDER BY s.upvotes DESC
        LIMIT ?
    """, (args.limit,)).fetchall()

    if not signals:
        print("No untriaged signals. Run the harvester first.")
        return

    print(f"Tier 1 noise filter — {len(signals)} signals, model: {model}")
    print(f"{'='*60}")

    passed = 0
    filtered = 0

    for sig in signals:
        text = sig["raw_text"][:200].replace("\n", " ")
        print(f"\n  #{sig['id']} r/{sig['subreddit']} ({sig['upvotes']}↑): {text[:80]}...")

        is_signal, reason = tier1_filter(sig["raw_text"], model)

        if is_signal is None:
            print(f"    [skip] LLM error")
            continue

        if is_signal:
            # Create a triage entry for signals that pass
            db.execute("""
                INSERT INTO harvest_triage (signal_id, verdict, verdict_reason)
                VALUES (?, 'pending', ?)
            """, (sig["id"], f"Passed T1 filter: {reason}"))
            passed += 1
            print(f"    [PASS] {reason}")
        else:
            # Create a rejected triage entry
            db.execute("""
                INSERT INTO harvest_triage (signal_id, verdict, verdict_reason)
                VALUES (?, 'rejected', ?)
            """, (sig["id"], f"T1 noise filter: {reason}"))
            filtered += 1
            print(f"    [NOISE] {reason}")

    db.commit()

    print(f"\n{'='*60}")
    print(f"Tier 1 complete: {passed} passed, {filtered} filtered out")
    db.close()


def cmd_score(args):
    """Tier 2: Score signals that passed Tier 1."""
    db = get_db()
    default_model = args.model or TIER2_MODEL
    use_router = HAS_ROUTER and not args.model  # only use router if no explicit model

    # Get signals that passed T1 but don't have scores yet
    rows = db.execute("""
        SELECT t.id as triage_id, s.* FROM harvest_triage t
        JOIN harvest_signals s ON s.id = t.signal_id
        WHERE t.verdict = 'pending' AND t.market_size_score IS NULL
        ORDER BY s.upvotes DESC
        LIMIT ?
    """, (args.limit,)).fetchall()

    if not rows:
        print("No signals awaiting scoring. Run 'filter' first.")
        return

    router_info = " (Q-router adaptive)" if use_router else ""
    print(f"Tier 2 market scoring — {len(rows)} signals, model: {default_model}{router_info}")
    print(f"{'='*60}")

    for row in rows:
        text = row["raw_text"][:100].replace("\n", " ")

        # Q-router selects the best model for this signal type
        model = default_model
        route_decision = None
        if use_router and _q_router:
            route_decision = _q_router.route(row["raw_text"])
            routed_model = route_decision["route"]
            # Only use the routed model if it's a scoring-capable model (not skip/haiku)
            if routed_model in ("qwen2.5:14b", "qwen2.5:7b", "llama3.1:8b", "mistral:7b"):
                model = routed_model

        router_tag = f" [routed→{model}]" if route_decision else ""
        print(f"\n  #{row['id']} r/{row['subreddit']}{router_tag}: {text}...")

        scores = tier2_score(row["raw_text"], model)

        if not scores:
            print(f"    [skip] Scoring failed")
            continue

        valid, validation_errors = validate_tier2_output(scores)
        if not valid:
            print(f"    [invalid] {'; '.join(validation_errors)} — retrying once")
            scores = tier2_score(row["raw_text"], model)
            if scores:
                valid, validation_errors = validate_tier2_output(scores)
            if not scores or not valid:
                print(f"    [skip] Validation failed after retry: {validation_errors}")
                continue

        composite = compute_composite_score(scores)

        db.execute("""
            UPDATE harvest_triage SET
                market_size_score = ?,
                monetization_score = ?,
                build_complexity_score = ?,
                existing_solutions_score = ?,
                soy_leaf_fit_score = ?,
                composite_score = ?,
                existing_solutions = ?,
                monetization_model = ?,
                build_estimate = ?,
                target_audience = ?,
                updated_at = datetime('now')
            WHERE id = ?
        """, (
            scores.get("market_size_score"),
            scores.get("monetization_score"),
            scores.get("build_complexity_score"),
            scores.get("existing_solutions_score"),
            scores.get("soy_leaf_fit_score"),
            composite,
            scores.get("existing_solutions"),
            scores.get("monetization_model"),
            scores.get("build_estimate"),
            scores.get("target_audience"),
            row["triage_id"],
        ))

        # Also update the signal's extracted pain and industry
        if scores.get("extracted_pain"):
            db.execute("""
                UPDATE harvest_signals SET
                    extracted_pain = ?,
                    industry = ?
                WHERE id = ?
            """, (scores.get("extracted_pain"), scores.get("industry"), row["id"]))

        print(f"    Composite: {composite}/10 | Market: {scores.get('market_size_score')} | "
              f"Money: {scores.get('monetization_score')} | Build: {scores.get('build_complexity_score')} | "
              f"Gap: {scores.get('existing_solutions_score')}")
        print(f"    Pain: {scores.get('extracted_pain', '?')}")
        print(f"    Audience: {scores.get('target_audience', '?')}")

    db.commit()

    print(f"\n{'='*60}")
    print(f"Tier 2 scoring complete for {len(rows)} signals")
    db.close()


def cmd_review(args):
    """Show signals awaiting human review, ranked by composite score."""
    db = get_db()

    rows = db.execute("""
        SELECT t.*, s.raw_text, s.source_url, s.subreddit, s.upvotes, s.comment_count,
               s.extracted_pain, s.industry
        FROM harvest_triage t
        JOIN harvest_signals s ON s.id = t.signal_id
        WHERE t.verdict = 'pending' AND t.composite_score IS NOT NULL
        AND t.human_reviewed = 0
        ORDER BY t.composite_score DESC
        LIMIT ?
    """, (args.limit if hasattr(args, 'limit') else 20,)).fetchall()

    if not rows:
        print("No signals awaiting review. Run 'filter' and 'score' first.")
        return

    print(f"Signals awaiting human review (ranked by composite score)")
    print(f"{'='*70}")

    for row in rows:
        print(f"\n{'─'*70}")
        print(f"Signal #{row['signal_id']} | Composite: {row['composite_score']}/10 | r/{row['subreddit']} ({row['upvotes']}↑ {row['comment_count']}💬)")
        print(f"  Pain: {row['extracted_pain'] or '(not extracted)'}")
        print(f"  Industry: {row['industry'] or '?'}")
        print(f"  Market: {row['market_size_score']}/10 | Money: {row['monetization_score']}/10 | "
              f"Build: {row['build_complexity_score']}/10 | Gap: {row['existing_solutions_score']}/10 | "
              f"SoY fit: {row['soy_leaf_fit_score']}/10")
        print(f"  Existing: {row['existing_solutions'] or 'None known'}")
        print(f"  Model: {row['monetization_model'] or '?'} | Estimate: {row['build_estimate'] or '?'}")
        print(f"  Audience: {row['target_audience'] or '?'}")
        text = row['raw_text'][:250].replace("\n", " ")
        print(f"  Text: {text}...")
        print(f"  URL: {row['source_url']}")
        print(f"  → approve {row['signal_id']} | reject {row['signal_id']}")

    print(f"\n{'─'*70}")
    print(f"Showing {len(rows)} signals. Use 'approve <id>' or 'reject <id>' to decide.")
    db.close()


def cmd_approve(args):
    """Approve a signal for the build pipeline."""
    db = get_db()
    signal_id = args.signal_id

    triage = db.execute("""
        SELECT t.* FROM harvest_triage t WHERE t.signal_id = ?
    """, (signal_id,)).fetchone()

    if not triage:
        print(f"No triage entry for signal #{signal_id}")
        return

    # Q-router learning: human approved = LLM triage was good → reward
    if HAS_ROUTER and _q_router:
        signal = db.execute("SELECT raw_text FROM harvest_signals WHERE id = ?", (signal_id,)).fetchone()
        if signal:
            # High composite score + human approval = good routing decision
            reward = min(1.0, (triage["composite_score"] or 5.0) / 10.0 + 0.3)
            _q_router.learn(signal["raw_text"], TIER2_MODEL, reward)

    db.execute("""
        UPDATE harvest_triage SET
            verdict = 'approved',
            human_reviewed = 1,
            human_notes = ?,
            updated_at = datetime('now')
        WHERE signal_id = ?
    """, (args.notes or "Approved for build", signal_id))

    db.execute("""
        INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
        VALUES ('harvest_signal', ?, 'signal_approved', 'Signal approved for build pipeline', datetime('now'))
    """, (signal_id,))

    db.commit()
    print(f"Signal #{signal_id} approved for build pipeline.")
    db.close()


def cmd_reject(args):
    """Reject a signal."""
    db = get_db()
    signal_id = args.signal_id

    # Q-router learning: human rejected = LLM triage was wrong → negative reward
    if HAS_ROUTER and _q_router:
        signal = db.execute("SELECT raw_text FROM harvest_signals WHERE id = ?", (signal_id,)).fetchone()
        triage = db.execute("SELECT composite_score FROM harvest_triage WHERE signal_id = ?", (signal_id,)).fetchone()
        if signal and triage:
            # High composite score + human rejection = bad routing (LLM was overconfident)
            score = triage["composite_score"] or 5.0
            reward = max(-1.0, -score / 10.0)  # higher score = worse mistake
            _q_router.learn(signal["raw_text"], TIER2_MODEL, reward)

    db.execute("""
        UPDATE harvest_triage SET
            verdict = 'rejected',
            human_reviewed = 1,
            human_notes = ?,
            updated_at = datetime('now')
        WHERE signal_id = ?
    """, (args.reason or "Rejected by human review", signal_id))

    db.commit()
    print(f"Signal #{signal_id} rejected.")
    db.close()


def cmd_pipeline(args):
    """Run the full triage pipeline: filter → score → review."""
    print("Running full triage pipeline...\n")

    # Tier 1
    filter_args = argparse.Namespace(limit=args.limit, model=args.filter_model)
    cmd_filter(filter_args)

    # Tier 2
    print(f"\n{'='*60}")
    print("Moving to Tier 2 scoring...\n")
    score_args = argparse.Namespace(limit=args.limit, model=args.score_model)
    cmd_score(score_args)

    # Show review
    print(f"\n{'='*60}")
    print("Review queue:\n")
    review_args = argparse.Namespace(limit=20)
    cmd_review(review_args)


def main():
    parser = argparse.ArgumentParser(description="Signal Triage — multi-LLM evaluation pipeline")
    subparsers = parser.add_subparsers(dest="command")

    # filter
    p_filter = subparsers.add_parser("filter", help="Tier 1: noise filter")
    p_filter.add_argument("--limit", type=int, default=50)
    p_filter.add_argument("--model", help=f"Ollama model (default: {TIER1_MODEL})")

    # score
    p_score = subparsers.add_parser("score", help="Tier 2: market scoring")
    p_score.add_argument("--limit", type=int, default=20)
    p_score.add_argument("--model", help=f"Ollama model (default: {TIER2_MODEL})")

    # review
    p_review = subparsers.add_parser("review", help="Show signals awaiting human review")
    p_review.add_argument("--limit", type=int, default=20)

    # approve
    p_approve = subparsers.add_parser("approve", help="Approve a signal for build")
    p_approve.add_argument("signal_id", type=int)
    p_approve.add_argument("--notes", help="Optional notes")

    # reject
    p_reject = subparsers.add_parser("reject", help="Reject a signal")
    p_reject.add_argument("signal_id", type=int)
    p_reject.add_argument("reason", nargs="?", default="Rejected by human review")

    # pipeline
    p_pipe = subparsers.add_parser("pipeline", help="Run full triage pipeline")
    p_pipe.add_argument("--limit", type=int, default=50)
    p_pipe.add_argument("--filter-model", help=f"Tier 1 model (default: {TIER1_MODEL})")
    p_pipe.add_argument("--score-model", help=f"Tier 2 model (default: {TIER2_MODEL})")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    commands = {
        "filter": cmd_filter,
        "score": cmd_score,
        "review": cmd_review,
        "approve": cmd_approve,
        "reject": cmd_reject,
        "pipeline": cmd_pipeline,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
