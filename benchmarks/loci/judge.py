"""
judge.py — Claude Opus judge for the loci benchmark.

Reads arm_results from results.db, sends each (prompt, gold, context, answer)
tuple to Claude Opus via urllib (no anthropic SDK), parses the structured
rubric response, stores it in the judge_scores table.

The judge sees the context that was delivered alongside the answer (Sara's
anti-bias measure from the panel design phase). Without that, a beautiful-but-
hallucinated answer over thin context could outscore a correct-but-terse
answer over rich context. With it, the judge can call out "the context
contained X but the answer ignored it."

Blind labeling: arms are scrambled per prompt (random A/B/C → 1/2/3 mapping
chosen at judge time, unscrambled at report time). The judge sees only the
scrambled labels so its scoring can't be biased by knowing which arm produced
which answer.

Conventions inherited from runner.py:
- Python stdlib only (urllib for the Anthropic API, sqlite3 for results, json).
- API key from ANTHROPIC_API_KEY environment variable.
- Defensive error handling — a failed judge call records the error and moves on,
  it doesn't abort the whole judging run.

Usage (as a module):
    from judge import judge_run
    judge_run(run_id="20260410_141453")

Or via runner.py's `judge` subcommand:
    python3 benchmarks/loci/runner.py judge <run_id>
"""

import json
import os
import random
import sqlite3
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


# ─── Configuration ───────────────────────────────────────────────────

DEFAULT_JUDGE_MODEL = "claude-opus-4-6"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"
JUDGE_MAX_TOKENS = 2000
JUDGE_TEMPERATURE = 0.0  # deterministic-as-possible scoring
JUDGE_TIMEOUT = 120  # seconds per judge call
RATE_LIMIT_DELAY_S = 0.5  # gentle pacing between calls


# ─── Judge prompt template ───────────────────────────────────────────
# The judge sees: question, gold facts, context delivered, answer.
# The judge does NOT see: which arm produced the answer (blinded by
# scrambled labels) or any of the design notes / expected gradients.

JUDGE_SYSTEM = """You are evaluating answers from a personal data system. Your job is to score how well an answer addresses a user's question, given the context that was provided to the answering model and the gold facts the answer should ideally contain.

Score on a 1-5 scale per dimension. Be strict but fair. Reward honesty about data gaps over confident fabrication. Penalize hallucinations heavily — a hallucinated fact is worse than a missing fact.

Output STRICT JSON ONLY. No markdown, no preamble, no explanation outside the JSON. Your entire response must be a single JSON object that parses cleanly."""


JUDGE_PROMPT_TEMPLATE = """## QUESTION
{question}

## GOLD FACTS (what the answer should ideally contain)
{gold_facts}

{honest_answer_block}{negative_signals_block}## CONTEXT THAT WAS PROVIDED TO THE ANSWERING MODEL
{context}

## THE ANSWER YOU ARE SCORING
{answer}

---

Score the answer on these dimensions and return ONE JSON object with ALL of these fields:

{{
  "relevance": <1-5 integer>,
  "rationale_relevance": "<1-2 sentence explanation>",
  "completeness": <1-5 integer, scored against the GOLD FACTS>,
  "rationale_completeness": "<1-2 sentence explanation, naming which gold facts were covered or missed>",
  "surfaced_non_obvious": <1-5 integer, did the answer identify anything useful not directly named in the question>,
  "rationale_surfaced": "<1-2 sentence explanation>",
  "hallucination_count": <integer count of statements in the answer not supported by the context>,
  "rationale_hallucinations": "<list each hallucinated statement, or 'none' if zero>",
  "one_liner_right": "<single sentence: what does this answer get RIGHT>",
  "one_liner_missed": "<single sentence: what does this answer MISS>",
  "judge_confidence": <1-5 integer, your confidence in this rating>
}}

CRITICAL: Return only the JSON object. No text before or after. Start with {{ and end with }}."""


def _fmt_gold_facts(gold: dict) -> str:
    """Render a prompts.json gold object as a bulleted list for the judge."""
    facts = gold.get("facts", [])
    if not facts:
        return "(no facts specified)"
    return "\n".join(f"- {f}" for f in facts)


def _fmt_honest_answer_block(gold: dict) -> str:
    notes = gold.get("honest_answer_notes")
    if not notes:
        return ""
    return f"## HONEST ANSWER NOTES\n{notes}\n\n"


def _fmt_negative_signals_block(gold: dict) -> str:
    negatives = gold.get("negative_signals", [])
    if not negatives:
        return ""
    items = "\n".join(f"- {n}" for n in negatives)
    return f"## NEGATIVE SIGNALS (these would be hallucinations)\n{items}\n\n"


def build_judge_prompt(question: str, gold: dict, context: str, answer: str) -> str:
    return JUDGE_PROMPT_TEMPLATE.format(
        question=question,
        gold_facts=_fmt_gold_facts(gold),
        honest_answer_block=_fmt_honest_answer_block(gold),
        negative_signals_block=_fmt_negative_signals_block(gold),
        context=context,
        answer=answer,
    )


# ─── Anthropic API call (urllib, no SDK) ─────────────────────────────

def call_claude(model: str, system: str, user: str,
                api_key: str, max_tokens: int = JUDGE_MAX_TOKENS,
                temperature: float = JUDGE_TEMPERATURE,
                timeout: int = JUDGE_TIMEOUT) -> tuple:
    """POST to Anthropic /v1/messages. Returns (text_response, elapsed_ms, error)."""
    payload = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }).encode()

    req = Request(
        ANTHROPIC_API_URL,
        data=payload,
        headers={
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_API_VERSION,
            "content-type": "application/json",
        },
    )
    start = time.time()
    try:
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
            # Response shape: {"content": [{"type": "text", "text": "..."}], ...}
            blocks = data.get("content", [])
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            return text.strip(), int((time.time() - start) * 1000), None
    except HTTPError as e:
        body = e.read().decode(errors="replace")[:500]
        return None, int((time.time() - start) * 1000), f"HTTP {e.code}: {body}"
    except URLError as e:
        return None, int((time.time() - start) * 1000), f"URLError: {e}"
    except Exception as e:
        return None, int((time.time() - start) * 1000), f"{type(e).__name__}: {e}"


# ─── JSON parsing (defensive) ────────────────────────────────────────

def parse_judge_response(response: str) -> tuple:
    """Try to extract a JSON object from a judge response. Returns (parsed_dict, error)."""
    if not response:
        return None, "empty response"
    cleaned = response.strip()

    # Strip markdown fences if present
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    # Try direct parse first
    try:
        return json.loads(cleaned), None
    except json.JSONDecodeError:
        pass

    # Fall back to first {...} block
    start = cleaned.find("{")
    end = cleaned.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(cleaned[start:end]), None
        except json.JSONDecodeError as e:
            return None, f"JSON parse failed: {e}"

    return None, "no JSON object found in response"


def validate_judge_dict(d: dict) -> tuple:
    """Check that required fields are present and well-typed. Returns (ok, error)."""
    required_int = ["relevance", "completeness", "surfaced_non_obvious",
                    "hallucination_count", "judge_confidence"]
    required_str = ["rationale_relevance", "rationale_completeness",
                    "rationale_surfaced", "rationale_hallucinations",
                    "one_liner_right", "one_liner_missed"]

    missing = []
    for k in required_int + required_str:
        if k not in d:
            missing.append(k)
    if missing:
        return False, f"missing fields: {missing}"

    for k in required_int:
        try:
            int(d[k])
        except (ValueError, TypeError):
            return False, f"field '{k}' is not an integer: {d[k]!r}"

    return True, None


# ─── Schema for judge_scores table ───────────────────────────────────

JUDGE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS judge_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    prompt_id TEXT NOT NULL,
    arm_id TEXT NOT NULL,
    judge_model TEXT NOT NULL,
    blind_label TEXT NOT NULL,
    relevance INTEGER,
    completeness INTEGER,
    surfaced_non_obvious INTEGER,
    hallucination_count INTEGER,
    judge_confidence INTEGER,
    rationale_relevance TEXT,
    rationale_completeness TEXT,
    rationale_surfaced TEXT,
    rationale_hallucinations TEXT,
    one_liner_right TEXT,
    one_liner_missed TEXT,
    raw_judge_response TEXT,
    judge_elapsed_ms INTEGER,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(run_id, prompt_id, arm_id, judge_model)
);

CREATE INDEX IF NOT EXISTS idx_judge_scores_run ON judge_scores(run_id);
CREATE INDEX IF NOT EXISTS idx_judge_scores_prompt ON judge_scores(prompt_id);
"""


def init_judge_schema(db: sqlite3.Connection) -> None:
    db.executescript(JUDGE_SCHEMA_SQL)
    db.commit()


# ─── Main judging loop ───────────────────────────────────────────────

def _load_prompts_by_id(prompts_path: str) -> dict:
    with open(prompts_path) as f:
        data = json.load(f)
    return {p["id"]: p for p in data["prompts"]}


def judge_run(
    run_id: str,
    results_db_path: str,
    prompts_path: str,
    judge_model: str = DEFAULT_JUDGE_MODEL,
    api_key: str = None,
    rerun_existing: bool = False,
) -> dict:
    """Judge all answers in a benchmark run.

    Args:
        run_id: The run to judge.
        results_db_path: Path to results.db.
        prompts_path: Path to prompts.json (for gold facts).
        judge_model: Anthropic model name.
        api_key: Anthropic API key. Falls back to ANTHROPIC_API_KEY env var.
        rerun_existing: If True, re-judge entries that already have scores.
                        If False (default), skip them.

    Returns:
        dict with stats: total, judged, skipped, errors.
    """
    if api_key is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("FATAL: ANTHROPIC_API_KEY not set in environment.", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(results_db_path):
        print(f"FATAL: results database not found at {results_db_path}", file=sys.stderr)
        sys.exit(1)

    prompts_by_id = _load_prompts_by_id(prompts_path)
    if not prompts_by_id:
        print("FATAL: no prompts loaded.", file=sys.stderr)
        sys.exit(1)

    db = sqlite3.connect(results_db_path)
    db.row_factory = sqlite3.Row
    init_judge_schema(db)

    # Verify the run exists
    run_row = db.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if not run_row:
        print(f"FATAL: no run found with id {run_id}", file=sys.stderr)
        sys.exit(1)

    # Pull arm results that have non-null answers (skip dry-run rows)
    answer_rows = db.execute(
        "SELECT * FROM arm_results WHERE run_id = ? AND answer IS NOT NULL "
        "ORDER BY prompt_id, arm_id",
        (run_id,),
    ).fetchall()

    if not answer_rows:
        print(f"No judgable rows in run {run_id} (was it a dry run?)", file=sys.stderr)
        return {"total": 0, "judged": 0, "skipped": 0, "errors": 0}

    # Build the blind label mapping per prompt: random A/B/C → 1/2/3
    rng = random.Random(run_id)  # deterministic per run
    blind_maps: dict = {}  # prompt_id -> {arm_id: blind_label}
    for prompt_id in {r["prompt_id"] for r in answer_rows}:
        labels = ["1", "2", "3"]
        rng.shuffle(labels)
        blind_maps[prompt_id] = dict(zip(["A", "B", "C"], labels))

    print(f"\n{'=' * 70}")
    print(f"JUDGING run {run_id} with {judge_model}")
    print(f"{'=' * 70}")
    print(f"Answer rows to judge: {len(answer_rows)}")
    print(f"Rerun existing scores: {rerun_existing}")
    print()

    total = len(answer_rows)
    judged = 0
    skipped = 0
    errors = 0

    for row in answer_rows:
        prompt_id = row["prompt_id"]
        arm_id = row["arm_id"]
        blind_label = blind_maps[prompt_id][arm_id]

        # Skip if already judged (unless rerun_existing)
        if not rerun_existing:
            existing = db.execute(
                "SELECT id FROM judge_scores WHERE run_id = ? AND prompt_id = ? "
                "AND arm_id = ? AND judge_model = ? AND error IS NULL",
                (run_id, prompt_id, arm_id, judge_model),
            ).fetchone()
            if existing:
                skipped += 1
                continue

        prompt_def = prompts_by_id.get(prompt_id)
        if not prompt_def:
            print(f"  [{prompt_id}/{arm_id}] SKIP — prompt not found in prompts.json")
            skipped += 1
            continue

        question = prompt_def["prompt"]
        gold = prompt_def["gold"]
        context = row["context"]
        answer = row["answer"]

        user_msg = build_judge_prompt(question, gold, context, answer)

        print(f"  [{prompt_id}/{arm_id}] (blind={blind_label})", end=" ", flush=True)
        raw, elapsed_ms, api_err = call_claude(
            judge_model, JUDGE_SYSTEM, user_msg, api_key,
        )

        if api_err:
            print(f"API ERROR ({elapsed_ms}ms): {api_err}")
            db.execute(
                "INSERT OR REPLACE INTO judge_scores "
                "(run_id, prompt_id, arm_id, judge_model, blind_label, "
                "raw_judge_response, judge_elapsed_ms, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, prompt_id, arm_id, judge_model, blind_label,
                 None, elapsed_ms, api_err),
            )
            db.commit()
            errors += 1
            time.sleep(RATE_LIMIT_DELAY_S)
            continue

        parsed, parse_err = parse_judge_response(raw)
        if parse_err:
            print(f"PARSE ERROR ({elapsed_ms}ms): {parse_err}")
            db.execute(
                "INSERT OR REPLACE INTO judge_scores "
                "(run_id, prompt_id, arm_id, judge_model, blind_label, "
                "raw_judge_response, judge_elapsed_ms, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, prompt_id, arm_id, judge_model, blind_label,
                 raw, elapsed_ms, parse_err),
            )
            db.commit()
            errors += 1
            time.sleep(RATE_LIMIT_DELAY_S)
            continue

        ok, validation_err = validate_judge_dict(parsed)
        if not ok:
            print(f"VALIDATION ERROR ({elapsed_ms}ms): {validation_err}")
            db.execute(
                "INSERT OR REPLACE INTO judge_scores "
                "(run_id, prompt_id, arm_id, judge_model, blind_label, "
                "raw_judge_response, judge_elapsed_ms, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, prompt_id, arm_id, judge_model, blind_label,
                 raw, elapsed_ms, validation_err),
            )
            db.commit()
            errors += 1
            time.sleep(RATE_LIMIT_DELAY_S)
            continue

        # Successful judge — store all the fields
        db.execute(
            "INSERT OR REPLACE INTO judge_scores "
            "(run_id, prompt_id, arm_id, judge_model, blind_label, "
            "relevance, completeness, surfaced_non_obvious, hallucination_count, "
            "judge_confidence, rationale_relevance, rationale_completeness, "
            "rationale_surfaced, rationale_hallucinations, one_liner_right, "
            "one_liner_missed, raw_judge_response, judge_elapsed_ms, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, prompt_id, arm_id, judge_model, blind_label,
             int(parsed["relevance"]), int(parsed["completeness"]),
             int(parsed["surfaced_non_obvious"]), int(parsed["hallucination_count"]),
             int(parsed["judge_confidence"]),
             parsed["rationale_relevance"], parsed["rationale_completeness"],
             parsed["rationale_surfaced"], parsed["rationale_hallucinations"],
             parsed["one_liner_right"], parsed["one_liner_missed"],
             raw, elapsed_ms, None),
        )
        db.commit()

        scores = (f"R{parsed['relevance']} C{parsed['completeness']} "
                  f"S{parsed['surfaced_non_obvious']} "
                  f"H{parsed['hallucination_count']} "
                  f"conf{parsed['judge_confidence']}")
        print(f"OK ({elapsed_ms}ms) {scores}")
        judged += 1

        # Update the runs row with the judge model used (idempotent)
        db.execute("UPDATE runs SET judge_model = ? WHERE run_id = ?",
                   (judge_model, run_id))
        db.commit()

        time.sleep(RATE_LIMIT_DELAY_S)

    print(f"\n{'=' * 70}")
    print(f"Judging complete: {judged}/{total} judged, {skipped} skipped, {errors} errors")
    print(f"{'=' * 70}\n")

    db.close()
    return {"total": total, "judged": judged, "skipped": skipped, "errors": errors}
