"""
test_subagent.py — Subagent path for the benchmark TEST MODEL step.

Mirrors judge_subagent.py but for the test-model role. Used when the test
model can't be reached via Ollama (e.g., we're using a Claude Code subagent
as the test model so the user's subscription quota covers the run instead
of an external API key).

The pattern is symmetric to judging:

  1. dump_test_package(run_id):
       Reads prompts.json, assembles context per arm via arms.py (using the
       same code path as runner.py's `run` command), writes a test package
       JSON file with all 51 (prompt × arm × assembled-context) entries.
       Creates the runs row in results.db and inserts arm_results rows with
       NULL answers — the subagent fills the answers in step 3.

  2. (orchestrator spawns subagent acting as test model)
       The orchestrator hands the package to a fresh subagent and asks it
       to read each entry's question + context and produce an answer in
       the same way the local Ollama models did. The subagent IS the test
       model — it doesn't call out to anything, it answers from its own
       capabilities. Writes test_answers_<run_id>.json.

  3. import_test_answers(run_id):
       Reads test_answers_<run_id>.json and UPDATEs arm_results rows
       with the answers + timing. After this step the run is judgable
       via the existing judge_subagent / runner.py judge-import path.

The test_model column in the runs row is set to a sentinel like
"claude-opus-4-6-subagent" so reports can distinguish runs whose test
model was a subagent from those whose test model was an Ollama model.

Conventions identical to judge_subagent.py:
  - Python stdlib only
  - Files written to benchmarks/loci/ alongside results.db
  - test_package_*.json and test_answers_*.json gitignored
  - Reuses arms.run_arm for context assembly so the parity cap applies
"""

import json
import os
import sqlite3
import sys
from datetime import datetime
from typing import Optional

# Make this script importable from benchmarks/loci/ regardless of cwd
_BENCH_DIR = os.path.dirname(os.path.abspath(__file__))
if _BENCH_DIR not in sys.path:
    sys.path.insert(0, _BENCH_DIR)

import arms  # noqa: E402


SUBAGENT_TEST_MODEL_LABEL = "claude-opus-4-6-subagent"
DEFAULT_MAX_CONTEXT_CHARS = 8000


# ─── Subagent instructions for the test-model role (embedded in package) ───
# Distinct from the judge instructions: the test subagent is producing
# answers, not scoring them. Its job is to behave like a model answering
# questions about a personal data system, using ONLY the context provided
# per entry. It must NOT use any prior knowledge or external research.

TEST_SUBAGENT_INSTRUCTIONS = """You are the TEST MODEL for an A/B/C benchmark experiment. Your job is to produce 51 answers — one per entry — to questions about a personal data system called Software of You (SoY). For each entry, you receive a question and a CONTEXT blob that was assembled by one of three context-assembly strategies. You must answer using ONLY the information in that context blob. Do not use prior knowledge. Do not invent facts. If the context doesn't contain enough information, say so explicitly rather than guessing.

The benchmark is comparing how three context-assembly strategies affect answer quality. Each entry has an `arm_id` of "A", "B", or "C", but you should NOT let that label influence how you answer. Treat every entry the same way: read the context, read the question, produce the best honest answer you can from the context alone. The arm label exists in the package for the orchestrator's bookkeeping — it has no semantic meaning to you as the test model.

For each entry, produce a JSON object with EXACTLY these fields:

  {
    "id": "<copy verbatim from input — format is prompt_id__arm_id>",
    "answer": "<your answer text — what a useful response to the question would be, based ONLY on the context>"
  }

Output format: a single JSON object with a top-level `answers` field containing an ARRAY of answer objects, one per input entry. Order should match input order.

  {
    "answers": [
      {"id": "R1__A", "answer": "..."},
      {"id": "R1__B", "answer": "..."},
      {"id": "R1__C", "answer": "..."},
      ...51 entries total...
    ]
  }

Write the result as a single JSON file using the Write tool. Do NOT print answers to stdout. Do NOT modify the input file. If you cannot answer an entry for any reason, include `{"id": "...", "error": "<reason>"}` instead of skipping it.

Critical answering guidance:
  - **Use ONLY the context.** Pretend you have no other knowledge of Alex, his projects, or his contacts. The benchmark measures whether each context-assembly strategy gave you enough to answer well — if you backfill from outside knowledge, you contaminate the comparison.
  - **Be willing to refuse.** "The context does not contain X" is a valid answer when X isn't in the context. Honest refusal beats confident fabrication.
  - **Match the style of the question.** A "remind me" prompt deserves a brief reminder. A "prep me for X" prompt deserves a structured prep brief. A "have I followed up with Y" prompt deserves a yes/no plus evidence.
  - **Don't speculate about what the context is missing.** Answer from what's there.
  - **Don't try to be clever about which arm produced your context.** Treat each entry as a fresh task.

Length guidance: aim for 100-500 characters per answer for most prompts. Longer is OK when the question genuinely warrants it (synthesis, prep). Don't pad.
"""


# ─── Schema bridge ────────────────────────────────────────────────────
# Use the same arm_results schema as runner.py. We rely on runner.SCHEMA_SQL
# being applied (init_results_db is called by runner.run, but we may be the
# first writer to results.db when the test path is used standalone).

_RESULTS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL UNIQUE,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    test_model TEXT NOT NULL,
    test_host TEXT NOT NULL,
    judge_model TEXT,
    soy_db_path TEXT NOT NULL,
    dry_run INTEGER NOT NULL DEFAULT 0,
    prompt_ids TEXT,
    arm_ids TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS arm_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    prompt_id TEXT NOT NULL,
    arm_id TEXT NOT NULL,
    context TEXT NOT NULL,
    context_chars INTEGER NOT NULL,
    answer TEXT,
    answer_elapsed_ms INTEGER,
    assembly_elapsed_ms INTEGER NOT NULL,
    total_elapsed_ms INTEGER,
    metadata TEXT,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(run_id, prompt_id, arm_id)
);

CREATE INDEX IF NOT EXISTS idx_arm_results_run ON arm_results(run_id);
CREATE INDEX IF NOT EXISTS idx_arm_results_prompt ON arm_results(prompt_id);
CREATE INDEX IF NOT EXISTS idx_arm_results_arm ON arm_results(arm_id);
"""


def _make_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# ─── Dump test package ────────────────────────────────────────────────

def dump_test_package(
    prompts_path: str,
    soy_db_path: str,
    results_db_path: str,
    output_dir: Optional[str] = None,
    test_model_label: str = SUBAGENT_TEST_MODEL_LABEL,
    test_host_label: str = "subagent",
    arm_ids: Optional[list] = None,
    prompt_ids: Optional[list] = None,
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
    notes: Optional[str] = None,
) -> dict:
    """Assemble all (prompt × arm) contexts, create the run row, write the test package.

    Returns dict with run_id, package_path, and entry count.
    """
    if not os.path.exists(prompts_path):
        raise FileNotFoundError(f"prompts file not found: {prompts_path}")
    if not os.path.exists(soy_db_path):
        raise FileNotFoundError(f"SoY database not found: {soy_db_path}")

    if output_dir is None:
        output_dir = os.path.dirname(results_db_path)
    os.makedirs(output_dir, exist_ok=True)

    with open(prompts_path) as f:
        all_prompts = json.load(f)["prompts"]

    if prompt_ids:
        wanted = set(prompt_ids)
        prompts_list = [p for p in all_prompts if p["id"] in wanted]
    else:
        prompts_list = all_prompts

    arms_to_run = arm_ids or list(arms.ARMS.keys())
    for a in arms_to_run:
        if a not in arms.ARMS:
            raise ValueError(f"Unknown arm: {a}")

    run_id = _make_run_id()

    # Initialize results.db schema, insert the run row
    db = sqlite3.connect(results_db_path)
    db.row_factory = sqlite3.Row
    db.executescript(_RESULTS_SCHEMA_SQL)
    db.execute(
        "INSERT INTO runs (run_id, test_model, test_host, soy_db_path, dry_run, "
        "prompt_ids, arm_ids, notes) VALUES (?, ?, ?, ?, 0, ?, ?, ?)",
        (run_id, test_model_label, test_host_label, soy_db_path,
         ",".join(p["id"] for p in prompts_list),
         ",".join(arms_to_run),
         notes),
    )
    db.commit()

    # Assemble context per (prompt × arm) and insert arm_results rows with NULL answers
    entries = []
    for prompt in prompts_list:
        for arm_id in arms_to_run:
            arm_result = arms.run_arm(arm_id, soy_db_path, prompt, max_chars=max_context_chars)
            entries.append({
                "id": f"{prompt['id']}__{arm_id}",
                "prompt_id": prompt["id"],
                "arm_id": arm_id,
                "bucket": prompt.get("bucket", "unknown"),
                "question": prompt.get("prompt", ""),
                "gold_facts": prompt.get("gold", {}).get("facts", []),
                "honest_answer_notes": prompt.get("gold", {}).get("honest_answer_notes"),
                "negative_signals": prompt.get("gold", {}).get("negative_signals", []),
                "context": arm_result.context,
                "context_chars": arm_result.context_chars,
                "metadata": arm_result.metadata,
            })

            db.execute(
                "INSERT OR REPLACE INTO arm_results "
                "(run_id, prompt_id, arm_id, context, context_chars, "
                "answer, answer_elapsed_ms, assembly_elapsed_ms, total_elapsed_ms, "
                "metadata, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, prompt["id"], arm_id, arm_result.context,
                 arm_result.context_chars,
                 None, None, arm_result.elapsed_ms, arm_result.elapsed_ms,
                 json.dumps(arm_result.metadata), arm_result.error),
            )
    db.commit()
    db.close()

    package = {
        "version": "1.0",
        "run_id": run_id,
        "test_model": test_model_label,
        "test_host": test_host_label,
        "test_path": "subagent",
        "instructions": TEST_SUBAGENT_INSTRUCTIONS,
        "n_entries": len(entries),
        "entries": entries,
    }

    package_path = os.path.join(output_dir, f"test_package_{run_id}.json")
    with open(package_path, "w") as f:
        json.dump(package, f, indent=2)

    return {
        "run_id": run_id,
        "package_path": package_path,
        "n_entries": len(entries),
        "n_prompts": len(prompts_list),
        "arms": arms_to_run,
    }


# ─── Import test answers ──────────────────────────────────────────────

def import_test_answers(
    run_id: str,
    answers_path: str,
    results_db_path: str,
) -> dict:
    """Read the subagent's answers file, UPDATE arm_results rows with the answers."""
    if not os.path.exists(answers_path):
        raise FileNotFoundError(f"answers file not found: {answers_path}")
    if not os.path.exists(results_db_path):
        raise FileNotFoundError(f"results database not found: {results_db_path}")

    with open(answers_path) as f:
        raw = f.read()

    # Try direct parse first
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Strip markdown fences and retry
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
        # Find first {...} block
        start = cleaned.find("{")
        end = cleaned.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(cleaned[start:end])
        else:
            raise ValueError(f"failed to parse answers file as JSON")

    if isinstance(parsed, dict) and "answers" in parsed:
        answer_list = parsed["answers"]
    elif isinstance(parsed, list):
        answer_list = parsed
    else:
        raise ValueError(
            f"answers file must contain a JSON array or {{\"answers\": [...]}} "
            f"object, got {type(parsed).__name__}"
        )

    db = sqlite3.connect(results_db_path)
    db.row_factory = sqlite3.Row

    updated = 0
    skipped = 0
    errors = 0

    for ans in answer_list:
        sid = ans.get("id")
        if not sid or "__" not in sid:
            print(f"  SKIP: missing or malformed id: {sid!r}", file=sys.stderr)
            skipped += 1
            continue
        prompt_id, arm_id = sid.split("__", 1)

        if ans.get("error"):
            db.execute(
                "UPDATE arm_results SET answer = ?, error = ? "
                "WHERE run_id = ? AND prompt_id = ? AND arm_id = ?",
                (None, ans["error"], run_id, prompt_id, arm_id),
            )
            errors += 1
            db.commit()
            continue

        answer_text = ans.get("answer")
        if not answer_text:
            print(f"  SKIP: empty answer for {sid}", file=sys.stderr)
            skipped += 1
            continue

        # Update the row. Note: we don't have answer_elapsed_ms because the
        # subagent doesn't time itself per entry — set to NULL. assembly_elapsed_ms
        # is preserved from the dump step.
        cur = db.execute(
            "UPDATE arm_results SET answer = ?, answer_elapsed_ms = NULL, error = NULL "
            "WHERE run_id = ? AND prompt_id = ? AND arm_id = ?",
            (answer_text, run_id, prompt_id, arm_id),
        )
        if cur.rowcount == 0:
            print(f"  SKIP: no row found for {sid}", file=sys.stderr)
            skipped += 1
        else:
            updated += 1
        db.commit()

    db.execute(
        "UPDATE runs SET completed_at = datetime('now') WHERE run_id = ?",
        (run_id,),
    )
    db.commit()
    db.close()

    return {
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "total": len(answer_list),
    }
