"""
judge_subagent.py — Subagent path for the loci benchmark judge.

Alternative to judge.py's Anthropic API path. Used when the user doesn't have
an API key but does have a Claude Code subscription, which covers running
Claude subagents inside Claude Code itself. The subagent is the same Opus
model class as the API call would have been, with one important advantage:
it has fresh context. No prior exposure to the loci value prop or anything
else from the orchestrating conversation. That's a stronger guarantee against
judge bias than a stateless API call by a model that's never heard of loci
either.

Three-step flow:

  1. dump_package(run_id):
       Reads arm_results from results.db, generates two files:
         - package_<run_id>.json   — data for the subagent (blinded)
         - blind_map_<run_id>.json — arm_id ↔ blind_label mapping (kept private)
       The package contains all 51 entries with random per-prompt blind
       labels (1/2/3 instead of A/B/C). The subagent never sees arm IDs.

  2. (orchestrator spawns subagent)
       The orchestrator (this Claude Code session) hands the package path
       and the rubric to a fresh subagent and asks it to score each entry,
       writing results to scores_<run_id>.json. The subagent's instructions
       are inlined in the package's `instructions` field for self-contained
       reproducibility — anyone can re-run the subagent step against the
       same package and get a comparable result.

  3. import_scores(run_id):
       Reads scores_<run_id>.json, looks up the blind label mapping from
       the private blind_map file, recovers each (prompt_id, arm_id), and
       inserts into the judge_scores table using the same schema as judge.py.
       After this step, report.py works unchanged.

Conventions:
- Reuses init_judge_schema, parse_judge_response, validate_judge_dict from
  judge.py so the schema and validation are identical regardless of which
  judge path was used.
- Files are written to benchmarks/loci/ alongside results.db. They are
  gitignored (regenerable from results.db + a subagent re-run).
- The blind_map file is the only secret — DO NOT share it with the subagent.
"""

import json
import os
import random
import sqlite3
import sys
from typing import Optional

# Make this script importable from benchmarks/loci/ regardless of cwd
_BENCH_DIR = os.path.dirname(os.path.abspath(__file__))
if _BENCH_DIR not in sys.path:
    sys.path.insert(0, _BENCH_DIR)

import judge  # noqa: E402  — for init_judge_schema, parse_judge_response, validate_judge_dict


SUBAGENT_JUDGE_MODEL = "claude-opus-4-6-subagent"


# ─── Subagent instructions (embedded in the package) ─────────────────
# These are also passed verbatim to the subagent at spawn time. Inlining
# them in the package means the package is self-contained: anyone (or any
# future subagent) can read it and understand the task without external
# context.

SUBAGENT_INSTRUCTIONS = """You are an LLM judge for an A/B/C benchmark experiment. You will read a JSON file containing entries — each entry is a (question, gold facts, context, answer) tuple from a personal data system called Software of You (SoY). Your job is to score each answer against its gold facts using a structured rubric.

The benchmark compares three context-assembly strategies. Each entry has a "blind_label" of "1", "2", or "3" — these are randomized PER QUESTION, so the same blind label means different strategies for different questions. You have no way to know which strategy produced which answer, and you should not try to guess. Score each answer on its own merits against the gold facts.

## Impartiality requirement (READ CAREFULLY)

You may be judging answers produced by a model from the same family or version as yourself. This is a real methodological concern: a model judging answers it might itself have generated could be biased toward those answers' style, vocabulary, or reasoning patterns. **You must actively counteract this.**

Treat every answer as if it came from a stranger whose judgment you have no investment in. Specifically:
  - Do NOT favor answers that "sound like how I would say it." Stylistic similarity is not quality.
  - Do NOT excuse omissions or hedging just because they feel familiar. If an answer misses a gold fact, mark it down even if the omission is one you might have made yourself.
  - Do NOT round up on confidence calls. If you're between a 3 and a 4, give a 3.
  - Reward concrete fact coverage and honest refusal. Penalize fluent fabrication and unsupported speculation, regardless of how confident the answer sounds.
  - When in doubt between two answers, favor the one that names more specific facts from the gold list — not the one that reads more naturally.

If you find yourself drafting a high score because the answer "is well written" or "shows good reasoning," stop and check whether that score is justified by the gold-fact coverage and the absence of hallucinations. Style is not signal here.

For each entry, produce a JSON object with these EXACT fields and types:

  {
    "id": "<copy verbatim from input>",
    "relevance": <integer 1-5>,
    "rationale_relevance": "<one or two sentence explanation>",
    "completeness": <integer 1-5, scored against the gold_facts list>,
    "rationale_completeness": "<one or two sentences naming which gold facts were covered or missed by id or paraphrase>",
    "surfaced_non_obvious": <integer 1-5, did the answer identify anything useful not directly named in the question>,
    "rationale_surfaced": "<one or two sentence explanation>",
    "hallucination_count": <integer count, can be 0>,
    "rationale_hallucinations": "<list each hallucinated statement, or the literal string 'none'>",
    "one_liner_right": "<single sentence: what does this answer get RIGHT>",
    "one_liner_missed": "<single sentence: what does this answer MISS>",
    "judge_confidence": <integer 1-5, your confidence in this rating>
  }

Scoring guidance:
  - Be strict but fair. A 5 means the answer is genuinely excellent against the gold facts. A 3 means competent but missing important things. A 1 means the answer fails the question.
  - Reward honesty about data gaps over confident fabrication. If gold has `honest_answer_notes` saying the right answer should flag a data gap, an answer that confidently invents details should score LOW on relevance even if it sounds polished.
  - Penalize hallucinations heavily — a hallucinated fact is worse than a missing fact. Count any statement in the answer not supported by the context as a hallucination, even if the statement happens to be true in the world.
  - For prompts with `negative_signals` (things that would be hallucinations), explicitly check for each one in the answer.
  - Use the `context` field as ground truth for what the answer COULD have known. Penalize answers that miss things present in the context.
  - judge_confidence should be lower (1-2) when the answer is borderline or the gold is ambiguous, higher (4-5) when scoring is clear-cut.

Output format: a single JSON object with a top-level `scores` field containing an ARRAY of score objects, one per input entry. The order should match the input order. Example:

  {
    "scores": [
      {"id": "R1__1", "relevance": 4, "rationale_relevance": "...", ...},
      {"id": "R1__2", "relevance": 3, "rationale_relevance": "...", ...},
      ...
    ]
  }

Write the result as a single JSON file using the Write tool. Do NOT print scores to stdout. Do NOT modify the input file. If you cannot score an entry for any reason, include an entry with `"error": "<reason>"` in the scores array instead of skipping it — the orchestrator needs every input id to appear in the output.
"""


# ─── Dump: results.db → judge package + blind map ────────────────────

def _load_run(db: sqlite3.Connection, run_id: str) -> dict:
    """Pull run metadata + arm_results for the given run_id."""
    run_row = db.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if not run_row:
        raise ValueError(f"no run found with id {run_id}")
    arm_rows = db.execute(
        "SELECT * FROM arm_results WHERE run_id = ? AND answer IS NOT NULL "
        "ORDER BY prompt_id, arm_id",
        (run_id,),
    ).fetchall()
    return {"run": dict(run_row), "arms": [dict(r) for r in arm_rows]}


def _build_blind_map(arm_rows: list, run_id: str) -> dict:
    """For each prompt, randomize A/B/C → 1/2/3 with a seed derived from run_id.

    Returns: {prompt_id: {arm_id: blind_label}}
    Deterministic per run_id so re-runs of the dump produce the same mapping.
    """
    rng = random.Random(run_id)
    by_prompt: dict = {}
    for r in arm_rows:
        by_prompt.setdefault(r["prompt_id"], set()).add(r["arm_id"])

    blind_map: dict = {}
    for prompt_id, arms in by_prompt.items():
        labels = ["1", "2", "3"]
        rng.shuffle(labels)
        # Map sorted arm ids to shuffled labels so the assignment is reproducible
        arm_list = sorted(arms)
        blind_map[prompt_id] = dict(zip(arm_list, labels[:len(arm_list)]))
    return blind_map


def dump_package(
    run_id: str,
    results_db_path: str,
    prompts_path: str,
    output_dir: Optional[str] = None,
) -> dict:
    """Generate judge package + blind map files for a run.

    Returns dict with paths to the two output files and a summary.
    """
    if not os.path.exists(results_db_path):
        raise FileNotFoundError(f"results database not found: {results_db_path}")
    if not os.path.exists(prompts_path):
        raise FileNotFoundError(f"prompts file not found: {prompts_path}")

    if output_dir is None:
        output_dir = os.path.dirname(results_db_path)
    os.makedirs(output_dir, exist_ok=True)

    db = sqlite3.connect(results_db_path)
    db.row_factory = sqlite3.Row
    try:
        data = _load_run(db, run_id)
    finally:
        db.close()

    if not data["arms"]:
        raise ValueError(
            f"run {run_id} has no judgable rows (was it a dry run?). "
            f"Run `runner.py run` first to produce real answers."
        )

    with open(prompts_path) as f:
        prompts_by_id = {p["id"]: p for p in json.load(f)["prompts"]}

    blind_map = _build_blind_map(data["arms"], run_id)

    # Build the entries list. Each entry's "id" is "<prompt_id>__<blind_label>"
    # so the subagent can key its responses without seeing the arm.
    entries = []
    for r in data["arms"]:
        prompt_id = r["prompt_id"]
        arm_id = r["arm_id"]
        blind_label = blind_map[prompt_id][arm_id]
        prompt_def = prompts_by_id.get(prompt_id, {})
        gold = prompt_def.get("gold", {})
        entries.append({
            "id": f"{prompt_id}__{blind_label}",
            "prompt_id": prompt_id,
            "blind_label": blind_label,
            "bucket": prompt_def.get("bucket", "unknown"),
            "question": prompt_def.get("prompt", ""),
            "gold_facts": gold.get("facts", []),
            "honest_answer_notes": gold.get("honest_answer_notes"),
            "negative_signals": gold.get("negative_signals", []),
            "context": r["context"],
            "answer": r["answer"],
        })

    # Sort entries by prompt_id, then blind_label so the file is human-readable
    entries.sort(key=lambda e: (e["prompt_id"], e["blind_label"]))

    package = {
        "version": "1.0",
        "run_id": run_id,
        "test_model": data["run"]["test_model"],
        "test_host": data["run"]["test_host"],
        "judge_path": "subagent",
        "instructions": SUBAGENT_INSTRUCTIONS,
        "n_entries": len(entries),
        "entries": entries,
    }

    package_path = os.path.join(output_dir, f"package_{run_id}.json")
    blind_map_path = os.path.join(output_dir, f"blind_map_{run_id}.json")

    with open(package_path, "w") as f:
        json.dump(package, f, indent=2)
    with open(blind_map_path, "w") as f:
        json.dump({
            "run_id": run_id,
            "blind_map": blind_map,
            "warning": "Do NOT share this file with the judge subagent. "
                       "It defeats the blind labeling.",
        }, f, indent=2)

    return {
        "package_path": package_path,
        "blind_map_path": blind_map_path,
        "n_entries": len(entries),
        "n_prompts": len(blind_map),
    }


# ─── Import: scores file → judge_scores table ────────────────────────

def import_scores(
    run_id: str,
    scores_path: str,
    blind_map_path: str,
    results_db_path: str,
    judge_model: str = SUBAGENT_JUDGE_MODEL,
) -> dict:
    """Read the subagent's scores file, map blind labels back, insert into judge_scores."""
    if not os.path.exists(scores_path):
        raise FileNotFoundError(f"scores file not found: {scores_path}")
    if not os.path.exists(blind_map_path):
        raise FileNotFoundError(f"blind map not found: {blind_map_path}")
    if not os.path.exists(results_db_path):
        raise FileNotFoundError(f"results database not found: {results_db_path}")

    with open(scores_path) as f:
        raw = f.read()

    # The subagent might wrap the JSON in markdown fences or include preamble.
    # Reuse the defensive parser from judge.py.
    parsed, parse_err = judge.parse_judge_response(raw)
    if parse_err:
        raise ValueError(f"failed to parse scores file: {parse_err}")

    # Accept either a top-level array or a {"scores": [...]} object
    if isinstance(parsed, dict) and "scores" in parsed:
        score_list = parsed["scores"]
    elif isinstance(parsed, list):
        score_list = parsed
    else:
        raise ValueError(
            f"scores file must contain a JSON array or {{\"scores\": [...]}} "
            f"object, got {type(parsed).__name__}"
        )

    with open(blind_map_path) as f:
        blind_map_data = json.load(f)
    blind_map = blind_map_data["blind_map"]

    # Reverse lookup: prompt_id → {blind_label → arm_id}
    label_to_arm: dict = {}
    for prompt_id, arm_to_label in blind_map.items():
        label_to_arm[prompt_id] = {label: arm for arm, label in arm_to_label.items()}

    db = sqlite3.connect(results_db_path)
    db.row_factory = sqlite3.Row
    judge.init_judge_schema(db)

    inserted = 0
    skipped = 0
    errors = 0

    for score in score_list:
        sid = score.get("id")
        if not sid or "__" not in sid:
            print(f"  SKIP: missing or malformed id: {sid!r}", file=sys.stderr)
            skipped += 1
            continue

        prompt_id, blind_label = sid.split("__", 1)
        arm_id = label_to_arm.get(prompt_id, {}).get(blind_label)
        if not arm_id:
            print(f"  SKIP: no arm mapping for {sid}", file=sys.stderr)
            skipped += 1
            continue

        # If the subagent reported an error for this entry, store it as an error row
        if score.get("error"):
            db.execute(
                "INSERT OR REPLACE INTO judge_scores "
                "(run_id, prompt_id, arm_id, judge_model, blind_label, "
                "raw_judge_response, error) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, prompt_id, arm_id, judge_model, blind_label,
                 json.dumps(score), score["error"]),
            )
            db.commit()
            errors += 1
            continue

        ok, validation_err = judge.validate_judge_dict(score)
        if not ok:
            print(f"  VALIDATION ERROR for {sid}: {validation_err}", file=sys.stderr)
            db.execute(
                "INSERT OR REPLACE INTO judge_scores "
                "(run_id, prompt_id, arm_id, judge_model, blind_label, "
                "raw_judge_response, error) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, prompt_id, arm_id, judge_model, blind_label,
                 json.dumps(score), validation_err),
            )
            db.commit()
            errors += 1
            continue

        db.execute(
            "INSERT OR REPLACE INTO judge_scores "
            "(run_id, prompt_id, arm_id, judge_model, blind_label, "
            "relevance, completeness, surfaced_non_obvious, hallucination_count, "
            "judge_confidence, rationale_relevance, rationale_completeness, "
            "rationale_surfaced, rationale_hallucinations, one_liner_right, "
            "one_liner_missed, raw_judge_response, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, prompt_id, arm_id, judge_model, blind_label,
             int(score["relevance"]), int(score["completeness"]),
             int(score["surfaced_non_obvious"]), int(score["hallucination_count"]),
             int(score["judge_confidence"]),
             score["rationale_relevance"], score["rationale_completeness"],
             score["rationale_surfaced"], score["rationale_hallucinations"],
             score["one_liner_right"], score["one_liner_missed"],
             json.dumps(score), None),
        )
        db.commit()
        inserted += 1

    db.execute("UPDATE runs SET judge_model = ? WHERE run_id = ?",
               (judge_model, run_id))
    db.commit()
    db.close()

    return {
        "inserted": inserted,
        "skipped": skipped,
        "errors": errors,
        "total": len(score_list),
    }
