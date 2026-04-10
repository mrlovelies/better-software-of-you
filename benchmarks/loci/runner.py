"""
runner.py — Orchestration for the loci layer benchmark.

Loads prompts.json, runs each prompt through each arm to assemble context,
sends the context + prompt to the test model (Mistral 7B on Razer by default),
stores everything in results.db.

This commit handles assembly and test-model invocation. The judge (Claude Opus
via the Anthropic API) and the markdown report generator land in subsequent
commits — keep them separate so a failure in the judge doesn't waste a run
of the test model.

Conventions inherited from benchmarks/gemma4/benchmark.py:
- Python stdlib only. urllib for Ollama, sqlite3 for results, json for everything.
- Self-contained. No virtualenv, no pip install.
- Tailscale hostnames hard-coded (same HOSTS dict shape).

Usage:
    # Assemble context + run test model on all prompts × all arms
    python3 benchmarks/loci/runner.py run

    # Dry run: assemble context only, no test model call (fast, free, useful for validation)
    python3 benchmarks/loci/runner.py run --dry-run

    # Filter to specific prompts
    python3 benchmarks/loci/runner.py run --prompts R1,R4,C1

    # Filter to specific arms
    python3 benchmarks/loci/runner.py run --arms A,C

    # Override test model
    python3 benchmarks/loci/runner.py run --test-model qwen2.5:14b --test-host lucy

    # Show summary of the last run
    python3 benchmarks/loci/runner.py status

    # Inspect all results for a specific run
    python3 benchmarks/loci/runner.py inspect <run_id>
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from urllib.error import URLError
from urllib.request import Request, urlopen

# Make this script importable from the benchmarks/loci/ dir AND runnable from anywhere
_BENCH_DIR = os.path.dirname(os.path.abspath(__file__))
if _BENCH_DIR not in sys.path:
    sys.path.insert(0, _BENCH_DIR)

import arms  # noqa: E402
import judge  # noqa: E402
import report  # noqa: E402


# ─── Configuration ───────────────────────────────────────────────────

# Tailscale hostnames — same shape as benchmarks/gemma4/benchmark.py
HOSTS = {
    "razer": "http://100.91.234.67:11434",   # Mistral 7B / soy-1 / Tier 1
    "lucy": "http://100.74.238.16:11434",    # Qwen 2.5 14B / Tier 2
    "legion": "http://100.69.255.78:11434",  # Gemma 4 / RTX 5080
    "local": "http://localhost:11434",
}

DEFAULT_TEST_MODEL = "mistral:7b"
DEFAULT_TEST_HOST = "razer"
DEFAULT_TEMPERATURE = 0.1
DEFAULT_MODEL_TIMEOUT = 300  # seconds

# Hard char-budget parity across arms — Diego Reyes' panel finding.
# Without this, arm C's larger context biases the judge by priors. Set to
# 0 to disable; we keep both modes available so the user can run with
# parity for the headline result and without parity for the diagnostic.
DEFAULT_MAX_CONTEXT_CHARS = 8000

PROMPTS_PATH = os.path.join(_BENCH_DIR, "prompts.json")
RESULTS_DB_PATH = os.path.join(_BENCH_DIR, "results.db")

DEFAULT_SOY_DB = os.path.expanduser("~/.local/share/software-of-you/soy.db")


# ─── results.db schema ───────────────────────────────────────────────

SCHEMA_SQL = """
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


def init_results_db() -> sqlite3.Connection:
    conn = sqlite3.connect(RESULTS_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


# ─── Test model invocation ───────────────────────────────────────────

def ollama_generate(host_url: str, model: str, prompt: str,
                    temperature: float = DEFAULT_TEMPERATURE,
                    timeout: int = DEFAULT_MODEL_TIMEOUT) -> tuple:
    """Call Ollama /api/generate. Returns (response_text, elapsed_ms, error)."""
    url = f"{host_url}/api/generate"
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature},
    }).encode()
    req = Request(url, data=payload, headers={"Content-Type": "application/json"})
    start = time.time()
    try:
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
            return data.get("response", "").strip(), int((time.time() - start) * 1000), None
    except URLError as e:
        return None, int((time.time() - start) * 1000), f"URLError: {e}"
    except Exception as e:
        return None, int((time.time() - start) * 1000), f"{type(e).__name__}: {e}"


def check_model_available(host_url: str, model: str, timeout: int = 10) -> bool:
    """Check if a model is loaded on the given Ollama host. Returns False on any error."""
    url = f"{host_url}/api/tags"
    try:
        with urlopen(Request(url), timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
            available = [m.get("name", "") for m in data.get("models", [])]
            stem = model.split(":")[0]
            return any(model == name or name.startswith(stem) for name in available)
    except Exception:
        return False


# ─── Prompt template ─────────────────────────────────────────────────

PROMPT_TEMPLATE = """You are answering a question for Alex about his personal data system (Software of You). Use ONLY the information in the CONTEXT below. Do not invent facts. If the context doesn't contain enough information to answer the question, say so explicitly rather than guessing.

CONTEXT:
{context}

QUESTION:
{question}

ANSWER:"""


def build_test_prompt(context: str, question: str) -> str:
    return PROMPT_TEMPLATE.format(context=context, question=question)


# ─── Prompt loading ──────────────────────────────────────────────────

def load_prompts() -> list:
    with open(PROMPTS_PATH) as f:
        data = json.load(f)
    return data["prompts"]


def filter_prompts(prompts: list, prompt_ids: list = None) -> list:
    if not prompt_ids:
        return prompts
    wanted = set(prompt_ids)
    out = [p for p in prompts if p["id"] in wanted]
    missing = wanted - {p["id"] for p in out}
    if missing:
        print(f"WARNING: prompt IDs not found: {sorted(missing)}", file=sys.stderr)
    return out


# ─── Main run loop ───────────────────────────────────────────────────

def make_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def run(
    test_model: str = DEFAULT_TEST_MODEL,
    test_host: str = DEFAULT_TEST_HOST,
    prompt_ids: list = None,
    arm_ids: list = None,
    dry_run: bool = False,
    soy_db: str = DEFAULT_SOY_DB,
    notes: str = None,
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
) -> str:
    """Run the benchmark. Returns the run_id for downstream judging/reporting."""
    if not os.path.exists(soy_db):
        print(f"FATAL: SoY database not found at {soy_db}", file=sys.stderr)
        sys.exit(1)

    if test_host not in HOSTS:
        print(f"FATAL: unknown test host '{test_host}'. Known: {list(HOSTS.keys())}",
              file=sys.stderr)
        sys.exit(1)

    host_url = HOSTS[test_host]

    # Pre-flight: check the test model is reachable (skip in dry run)
    if not dry_run:
        if not check_model_available(host_url, test_model):
            print(f"FATAL: model '{test_model}' not available on host '{test_host}' "
                  f"({host_url})", file=sys.stderr)
            print("Hint: run `ollama pull <model>` on that machine, "
                  "or pass --dry-run to skip the model call.", file=sys.stderr)
            sys.exit(1)

    prompts = filter_prompts(load_prompts(), prompt_ids)
    if not prompts:
        print("No prompts to run.", file=sys.stderr)
        sys.exit(1)

    arms_to_run = arm_ids or list(arms.ARMS.keys())
    for a in arms_to_run:
        if a not in arms.ARMS:
            print(f"FATAL: unknown arm '{a}'. Known: {list(arms.ARMS.keys())}",
                  file=sys.stderr)
            sys.exit(1)

    run_id = make_run_id()
    db = init_results_db()

    db.execute(
        "INSERT INTO runs (run_id, test_model, test_host, soy_db_path, dry_run, "
        "prompt_ids, arm_ids, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, test_model, test_host, soy_db, 1 if dry_run else 0,
         ",".join(p["id"] for p in prompts),
         ",".join(arms_to_run),
         notes),
    )
    db.commit()

    total = len(prompts) * len(arms_to_run)
    print(f"\n{'=' * 70}")
    print(f"LOCI BENCHMARK — run {run_id}")
    print(f"{'=' * 70}")
    print(f"Test model:   {test_model} on {test_host} ({host_url})")
    print(f"Prompts:      {len(prompts)} ({', '.join(p['id'] for p in prompts)})")
    print(f"Arms:         {', '.join(arms_to_run)}")
    print(f"Dry run:      {'YES (no test model calls)' if dry_run else 'no'}")
    if max_context_chars:
        print(f"Max context:  {max_context_chars} chars (parity across arms)")
    else:
        print(f"Max context:  unlimited (length confound NOT controlled)")
    print(f"SoY DB:       {soy_db}")
    print(f"Results DB:   {RESULTS_DB_PATH}")
    print(f"Total runs:   {total}")
    print()

    completed = 0
    errors = 0

    for prompt in prompts:
        print(f"\n--- {prompt['id']} ({prompt['bucket']}) ---")
        print(f"    {prompt['prompt'][:100]}")

        for arm_id in arms_to_run:
            label = f"  [{arm_id}]"

            # Step 1: assemble context via the arm (with optional char-budget parity)
            arm_result = arms.run_arm(arm_id, soy_db, prompt, max_chars=max_context_chars)
            assembly_ms = arm_result.elapsed_ms

            if arm_result.error:
                print(f"{label} ASSEMBLY ERROR: {arm_result.error}")
                db.execute(
                    "INSERT OR REPLACE INTO arm_results "
                    "(run_id, prompt_id, arm_id, context, context_chars, "
                    "answer, answer_elapsed_ms, assembly_elapsed_ms, total_elapsed_ms, "
                    "metadata, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (run_id, prompt["id"], arm_id, "", 0,
                     None, None, assembly_ms, assembly_ms,
                     json.dumps(arm_result.metadata), arm_result.error),
                )
                db.commit()
                errors += 1
                continue

            print(f"{label} context: {arm_result.context_chars} chars, "
                  f"{assembly_ms}ms assembly", end="")

            # Step 2: send to test model (skipped in dry run)
            answer = None
            answer_ms = None
            answer_error = None

            if not dry_run:
                test_prompt_text = build_test_prompt(arm_result.context, prompt["prompt"])
                answer, answer_ms, answer_error = ollama_generate(
                    host_url, test_model, test_prompt_text,
                )
                if answer_error:
                    print(f" → MODEL ERROR ({answer_ms}ms): {answer_error}")
                    errors += 1
                else:
                    print(f" → answer: {len(answer)} chars, {answer_ms}ms")
            else:
                print(" → [dry run, skipped]")

            total_ms = assembly_ms + (answer_ms or 0)

            db.execute(
                "INSERT OR REPLACE INTO arm_results "
                "(run_id, prompt_id, arm_id, context, context_chars, "
                "answer, answer_elapsed_ms, assembly_elapsed_ms, total_elapsed_ms, "
                "metadata, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, prompt["id"], arm_id, arm_result.context, arm_result.context_chars,
                 answer, answer_ms, assembly_ms, total_ms,
                 json.dumps(arm_result.metadata), answer_error),
            )
            db.commit()
            completed += 1

    db.execute("UPDATE runs SET completed_at = datetime('now') WHERE run_id = ?", (run_id,))
    db.commit()

    print(f"\n{'=' * 70}")
    print(f"Run {run_id} complete: {completed}/{total} succeeded, {errors} errors")
    print(f"{'=' * 70}\n")
    print(f"Next: judge with `python3 benchmarks/loci/runner.py judge {run_id}` "
          f"(once judge is implemented)")
    print(f"      inspect with `python3 benchmarks/loci/runner.py inspect {run_id}`")

    db.close()
    return run_id


# ─── Status / inspect ────────────────────────────────────────────────

def status() -> None:
    """Show summary of the last run."""
    if not os.path.exists(RESULTS_DB_PATH):
        print("No results database yet. Run `runner.py run` first.")
        return
    db = init_results_db()
    last = db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    if not last:
        print("No runs recorded yet.")
        return

    print(f"\nLast run: {last['run_id']}")
    print(f"  Started:    {last['started_at']}")
    print(f"  Completed:  {last['completed_at'] or '(in progress or aborted)'}")
    print(f"  Test model: {last['test_model']} on {last['test_host']}")
    print(f"  Dry run:    {'yes' if last['dry_run'] else 'no'}")
    print(f"  Prompts:    {last['prompt_ids']}")
    print(f"  Arms:       {last['arm_ids']}")

    summary = db.execute(
        "SELECT arm_id, COUNT(*) as n, "
        "AVG(context_chars) as avg_context, "
        "AVG(assembly_elapsed_ms) as avg_assembly_ms, "
        "AVG(answer_elapsed_ms) as avg_answer_ms, "
        "SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) as errors "
        "FROM arm_results WHERE run_id = ? GROUP BY arm_id ORDER BY arm_id",
        (last["run_id"],),
    ).fetchall()

    print(f"\n  Per-arm stats:")
    print(f"    {'arm':<5} {'n':>4} {'avg_chars':>10} {'avg_assembly':>14} "
          f"{'avg_answer':>12} {'errors':>8}")
    print(f"    {'-' * 5} {'-' * 4} {'-' * 10} {'-' * 14} {'-' * 12} {'-' * 8}")
    for row in summary:
        avg_ctx = f"{row['avg_context']:.0f}" if row["avg_context"] else "—"
        avg_asm = f"{row['avg_assembly_ms']:.0f}ms" if row["avg_assembly_ms"] else "—"
        avg_ans = f"{row['avg_answer_ms']:.0f}ms" if row["avg_answer_ms"] else "—"
        print(f"    {row['arm_id']:<5} {row['n']:>4} {avg_ctx:>10} {avg_asm:>14} "
              f"{avg_ans:>12} {row['errors']:>8}")

    db.close()


def inspect(run_id: str) -> None:
    """Show all results for a specific run."""
    if not os.path.exists(RESULTS_DB_PATH):
        print("No results database yet.")
        return
    db = init_results_db()

    run_row = db.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if not run_row:
        print(f"No run found with id {run_id}")
        return

    print(f"\n=== Run {run_id} ===")
    print(f"Test model: {run_row['test_model']} on {run_row['test_host']}")
    print(f"Started: {run_row['started_at']}")
    print(f"Completed: {run_row['completed_at'] or '(incomplete)'}")
    print()

    results = db.execute(
        "SELECT * FROM arm_results WHERE run_id = ? ORDER BY prompt_id, arm_id",
        (run_id,),
    ).fetchall()

    current_prompt = None
    for r in results:
        if r["prompt_id"] != current_prompt:
            current_prompt = r["prompt_id"]
            print(f"\n## {current_prompt}")
        err = f" ERROR: {r['error']}" if r["error"] else ""
        ans_summary = ""
        if r["answer"]:
            ans_summary = f", answer {len(r['answer'])} chars"
        print(f"  [{r['arm_id']}] context {r['context_chars']} chars, "
              f"assembly {r['assembly_elapsed_ms']}ms{ans_summary}{err}")

    db.close()


# ─── CLI ─────────────────────────────────────────────────────────────

def _parse_csv(value: str) -> list:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Loci layer benchmark runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Run the benchmark (assembly + test model)")
    p_run.add_argument("--test-model", default=DEFAULT_TEST_MODEL,
                       help=f"Test model name (default: {DEFAULT_TEST_MODEL})")
    p_run.add_argument("--test-host", default=DEFAULT_TEST_HOST,
                       choices=list(HOSTS.keys()),
                       help=f"Ollama host (default: {DEFAULT_TEST_HOST})")
    p_run.add_argument("--prompts", default="",
                       help="Comma-separated prompt IDs to run (default: all)")
    p_run.add_argument("--arms", default="",
                       help="Comma-separated arm IDs to run (default: A,B,C)")
    p_run.add_argument("--dry-run", action="store_true",
                       help="Assemble context but skip test model invocation")
    p_run.add_argument("--soy-db", default=DEFAULT_SOY_DB,
                       help=f"Path to soy.db (default: {DEFAULT_SOY_DB})")
    p_run.add_argument("--max-context-chars", type=int, default=DEFAULT_MAX_CONTEXT_CHARS,
                       help=f"Hard char cap on context per arm "
                            f"(default: {DEFAULT_MAX_CONTEXT_CHARS}, "
                            f"set to 0 for unlimited)")
    p_run.add_argument("--notes", default="",
                       help="Optional notes attached to the run record")

    sub.add_parser("status", help="Show summary of the last run")

    p_inspect = sub.add_parser("inspect", help="Show all results for a specific run")
    p_inspect.add_argument("run_id", help="The run ID to inspect")

    p_judge = sub.add_parser("judge", help="Judge an existing run with Claude Opus")
    p_judge.add_argument("run_id", help="The run ID to judge")
    p_judge.add_argument("--judge-model", default=judge.DEFAULT_JUDGE_MODEL,
                         help=f"Anthropic model name (default: {judge.DEFAULT_JUDGE_MODEL})")
    p_judge.add_argument("--rerun", action="store_true",
                         help="Re-judge entries that already have scores")

    p_report = sub.add_parser("report", help="Generate markdown report for a run")
    p_report.add_argument("run_id", help="The run ID to report on")
    p_report.add_argument("--output", default=None,
                          help="Output path (default: benchmarks/loci/report-<run_id>.md)")

    args = parser.parse_args()

    if args.cmd == "run":
        run(
            test_model=args.test_model,
            test_host=args.test_host,
            prompt_ids=_parse_csv(args.prompts),
            arm_ids=_parse_csv(args.arms),
            dry_run=args.dry_run,
            soy_db=args.soy_db,
            notes=args.notes,
            max_context_chars=args.max_context_chars,
        )
    elif args.cmd == "status":
        status()
    elif args.cmd == "inspect":
        inspect(args.run_id)
    elif args.cmd == "judge":
        judge.judge_run(
            run_id=args.run_id,
            results_db_path=RESULTS_DB_PATH,
            prompts_path=PROMPTS_PATH,
            judge_model=args.judge_model,
            rerun_existing=args.rerun,
        )
    elif args.cmd == "report":
        path = report.generate_report(
            run_id=args.run_id,
            results_db_path=RESULTS_DB_PATH,
            prompts_path=PROMPTS_PATH,
            output_path=args.output,
        )
        print(f"Report written to {path}")


if __name__ == "__main__":
    main()
