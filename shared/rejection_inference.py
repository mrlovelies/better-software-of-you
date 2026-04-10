#!/usr/bin/env python3
"""
Rejection Inference — When humans reject signals without a reason,
the LLM infers why by comparing the rejected signal against approved ones
and the rejection patterns already established.

Called by pipeline_cron.py after each run, or standalone.

Usage:
  python3 rejection_inference.py run [--limit=20]
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
MODEL = "qwen2.5:7b"  # fast model — this is background inference, not user-facing


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def ollama_generate(prompt):
    url = f"{OLLAMA_HOST}/api/generate"
    payload = json.dumps({
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 256},
    }).encode()
    req = Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
            return data.get("response", "").strip()
    except Exception as e:
        return None


def get_context(db):
    """Build context from approved and previously-reasoned rejections."""
    approved = db.execute("""
        SELECT s.extracted_pain, s.industry, s.subreddit
        FROM harvest_signals s
        JOIN harvest_triage t ON t.signal_id = s.id
        WHERE t.verdict = 'approved'
        LIMIT 10
    """).fetchall()

    reasoned_rejections = db.execute("""
        SELECT s.extracted_pain, t.human_notes
        FROM harvest_signals s
        JOIN harvest_triage t ON t.signal_id = s.id
        WHERE t.verdict = 'rejected' AND t.human_reviewed = 1
        AND t.human_notes IS NOT NULL AND t.human_notes NOT LIKE '%pending inference%'
        LIMIT 10
    """).fetchall()

    approved_text = "\n".join([
        f"  - [{r['industry'] or '?'}] {r['extracted_pain'] or '(no summary)'}"
        for r in approved
    ]) or "  (none yet)"

    rejected_text = "\n".join([
        f"  - {r['extracted_pain'] or '(no summary)'} — REASON: {r['human_notes']}"
        for r in reasoned_rejections
    ]) or "  (none yet)"

    return approved_text, rejected_text


def infer_harvest_rejection(db, queue_item, approved_text, rejected_text):
    """Infer why a harvest signal was rejected."""
    signal = db.execute("""
        SELECT s.*, t.composite_score, t.market_size_score, t.monetization_score,
               t.existing_solutions_score, t.existing_solutions
        FROM harvest_signals s
        JOIN harvest_triage t ON t.signal_id = s.id
        WHERE s.id = ?
    """, (queue_item["signal_id"],)).fetchone()

    if not signal:
        return None

    prompt = f"""A human reviewer rejected this signal from the demand-discovery pipeline without giving a reason. Based on the signal content, the scoring, and the patterns of what gets approved vs rejected, infer the most likely reason for rejection.

REJECTED SIGNAL:
  Text: {(signal['raw_text'] or '')[:500]}
  Pain: {signal['extracted_pain'] or 'N/A'}
  Industry: {signal['industry'] or 'N/A'}
  Subreddit: r/{signal['subreddit'] or '?'}
  Composite: {signal['composite_score'] or '?'}/10
  Existing solutions: {signal['existing_solutions'] or 'N/A'}

APPROVED SIGNALS (what the reviewer likes):
{approved_text}

PREVIOUS REJECTIONS WITH REASONS (what the reviewer doesn't like):
{rejected_text}

Respond with ONE concise sentence explaining the most likely rejection reason. Be specific — not "not relevant" but "personal relationship advice post, not a product opportunity" or "already well-served by Notion and Obsidian."

Reason:"""

    return ollama_generate(prompt)


def infer_competitive_rejection(db, queue_item, approved_text, rejected_text):
    """Infer why a competitive signal was rejected."""
    signal = db.execute("""
        SELECT * FROM competitive_signals WHERE id = ?
    """, (queue_item["signal_id"],)).fetchone()

    if not signal:
        return None

    prompt = f"""A human reviewer rejected this competitive intelligence signal without giving a reason. Infer the most likely reason.

REJECTED SIGNAL:
  Product: {signal['target_product'] or '?'}
  Complaint: {signal['complaint_summary'] or signal['raw_text'][:300]}
  Type: {signal['complaint_type'] or '?'}
  Composite: {signal['composite_score'] or '?'}/10

Respond with ONE concise sentence explaining the most likely rejection reason.

Reason:"""

    return ollama_generate(prompt)


def cmd_run(args):
    db = get_db()

    queue = db.execute("""
        SELECT * FROM rejection_inference_queue
        WHERE status = 'pending'
        ORDER BY created_at ASC
        LIMIT ?
    """, (args.limit,)).fetchall()

    if not queue:
        print("No pending inferences.")
        return

    print(f"Inferring rejection reasons for {len(queue)} signals...")

    approved_text, rejected_text = get_context(db)
    inferred = 0

    for item in queue:
        db.execute(
            "UPDATE rejection_inference_queue SET status = 'processing' WHERE id = ?",
            (item["id"],)
        )
        db.commit()

        if item["signal_type"] == "competitive":
            reason = infer_competitive_rejection(db, item, approved_text, rejected_text)
        else:
            reason = infer_harvest_rejection(db, item, approved_text, rejected_text)

        if reason:
            # Update the queue
            db.execute("""
                UPDATE rejection_inference_queue SET
                    status = 'done', inferred_reason = ?, model_used = ?, processed_at = datetime('now')
                WHERE id = ?
            """, (reason, MODEL, item["id"]))

            # Update the actual signal's human_notes with the inferred reason
            if item["signal_type"] == "competitive":
                db.execute("""
                    UPDATE competitive_signals SET human_notes = ?
                    WHERE id = ? AND (human_notes IS NULL OR human_notes LIKE '%pending inference%')
                """, (f"[inferred] {reason}", item["signal_id"]))
            else:
                db.execute("""
                    UPDATE harvest_triage SET human_notes = ?
                    WHERE signal_id = ? AND (human_notes IS NULL OR human_notes LIKE '%pending inference%')
                """, (f"[inferred] {reason}", item["signal_id"]))

            inferred += 1
            print(f"  #{item['signal_id']} ({item['signal_type']}): {reason[:100]}")
        else:
            db.execute(
                "UPDATE rejection_inference_queue SET status = 'failed', processed_at = datetime('now') WHERE id = ?",
                (item["id"],)
            )
            print(f"  #{item['signal_id']}: inference failed")

        db.commit()

    print(f"\nInferred {inferred}/{len(queue)} rejection reasons.")
    db.close()


def main():
    parser = argparse.ArgumentParser(description="Rejection Inference")
    subparsers = parser.add_subparsers(dest="command")
    p = subparsers.add_parser("run")
    p.add_argument("--limit", type=int, default=20)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    cmd_run(args)


if __name__ == "__main__":
    main()
