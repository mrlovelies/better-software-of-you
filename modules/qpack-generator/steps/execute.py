"""
Step 7: Execute — Run a QPack question against live data and optional LLM.

Also usable as a standalone executor:
    from steps.execute import execute_question
    result = execute_question("crm.who_priority_week")

Or as a pipeline step that executes all featured questions:
    p = Pipeline([ScanStep(), ..., ExecuteStep(question_ids=["crm.who_priority_week"])])
"""

import json
import sqlite3
import time
import urllib.request
import urllib.error
import sys
from pathlib import Path

_mod_dir = Path(__file__).resolve().parents[1]
if str(_mod_dir) not in sys.path:
    sys.path.insert(0, str(_mod_dir))
from pipeline import PipelineStep

DB_PATH = Path.home() / ".local" / "share" / "software-of-you" / "soy.db"
QPACK_DIR = Path(__file__).resolve().parents[3] / "qpacks"

# ──────────────────────────────────────────────────────────────────────
#  LLM Machine Config — ordered by preference (best-first)
# ──────────────────────────────────────────────────────────────────────

LLM_MACHINES = [
    {
        "name": "legion",
        "ip": "100.69.255.78",
        "port": 11434,
        "model": "gemma4:e2b",
    },
    {
        "name": "lucy",
        "ip": "100.74.238.16",
        "port": 11434,
        "model": "qwen2.5:14b",
    },
    {
        "name": "razer",
        "ip": "100.91.234.67",
        "port": 11434,
        "model": "mistral:7b",
    },
]


# ──────────────────────────────────────────────────────────────────────
#  Ollama helpers
# ──────────────────────────────────────────────────────────────────────

def _check_ollama_health(ip: str, port: int, timeout: float = 5.0) -> bool:
    """Check if an Ollama instance is reachable."""
    try:
        req = urllib.request.Request(f"http://{ip}:{port}/api/tags")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _pick_machine() -> dict | None:
    """Pick the first healthy LLM machine from the preference list."""
    for machine in LLM_MACHINES:
        if _check_ollama_health(machine["ip"], machine["port"]):
            return machine
    return None


def _call_ollama(
    ip: str,
    port: int,
    model: str,
    prompt: str,
    system: str = None,
    temperature: float = 0.3,
    timeout: float = 120.0,
) -> dict:
    """
    Send a generation request to Ollama.

    Returns dict with: response, tokens_in, tokens_out, duration_ms, model
    On failure returns dict with: error, model
    """
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature},
    }
    if system:
        payload["system"] = system

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"http://{ip}:{port}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {
            "error": f"HTTP {e.code}: {e.read().decode()[:200]}",
            "model": model,
        }
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return {
            "error": f"Connection failed: {e}",
            "model": model,
        }

    return {
        "response": data.get("response", ""),
        "tokens_in": data.get("prompt_eval_count", 0),
        "tokens_out": data.get("eval_count", 0),
        "duration_ms": int((time.time() - start) * 1000),
        "model": model,
    }


# ──────────────────────────────────────────────────────────────────────
#  Query execution helpers
# ──────────────────────────────────────────────────────────────────────

def _run_context_queries(question: dict, db: sqlite3.Connection) -> dict:
    """
    Execute all context_queries for a question.

    Returns a dict keyed by each query's 'key', with values being
    lists of dicts (row data).
    """
    context_data = {}
    for cq in question.get("context_queries", []):
        key = cq["key"]
        sql = cq["sql"]
        try:
            rows = db.execute(sql).fetchall()
            context_data[key] = [dict(row) for row in rows]
        except sqlite3.OperationalError as e:
            context_data[key] = {"error": str(e)}
    return context_data


def _rows_to_markdown(rows) -> str:
    """Convert a list of row dicts to a markdown table string."""
    # Handle error entries — must check before empty check (error dicts are truthy)
    if isinstance(rows, dict) and "error" in rows:
        return f"(query error: {rows['error']})"

    if not rows:
        return "(no data)"

    cols = list(rows[0].keys())

    # Header
    header = "| " + " | ".join(str(c) for c in cols) + " |"
    separator = "| " + " | ".join("---" for _ in cols) + " |"

    # Rows
    lines = [header, separator]
    for row in rows:
        vals = []
        for c in cols:
            v = row.get(c)
            vals.append(str(v) if v is not None else "—")
        lines.append("| " + " | ".join(vals) + " |")

    return "\n".join(lines)


def _assemble_prompt(question: dict, context_data: dict) -> str:
    """
    Fill the prompt_template with markdown-rendered context data.

    Template placeholders like {nudges} get replaced with the markdown
    table for context_data["nudges"].
    """
    template = question.get("prompt_template", "")
    if not template:
        return ""

    for key, rows in context_data.items():
        if isinstance(rows, list):
            md = _rows_to_markdown(rows)
        elif isinstance(rows, dict) and "error" in rows:
            md = f"(query error: {rows['error']})"
        else:
            md = str(rows)
        template = template.replace("{" + key + "}", md)

    return template


# ──────────────────────────────────────────────────────────────────────
#  QPack question loader
# ──────────────────────────────────────────────────────────────────────

def _find_question(question_id: str, qpack_dir: Path = QPACK_DIR) -> dict | None:
    """Find a question by ID across all QPack files."""
    if not qpack_dir.exists():
        return None

    for f in qpack_dir.glob("*.qpack.json"):
        try:
            data = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        for q in data.get("questions", []):
            if q.get("id") == question_id:
                # Attach persona from the parent QPack for LLM system prompt
                q["_persona"] = data.get("persona", {})
                return q
    return None


def _list_all_questions(qpack_dir: Path = QPACK_DIR) -> list[dict]:
    """Load all questions from all QPack files."""
    questions = []
    if not qpack_dir.exists():
        return questions

    for f in sorted(qpack_dir.glob("*.qpack.json")):
        try:
            data = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        persona = data.get("persona", {})
        for q in data.get("questions", []):
            q["_persona"] = persona
            questions.append(q)
    return questions


# ──────────────────────────────────────────────────────────────────────
#  Main executor
# ──────────────────────────────────────────────────────────────────────

def execute_question(
    question_id: str,
    db_path: Path = DB_PATH,
    qpack_dir: Path = QPACK_DIR,
) -> dict:
    """
    Execute a QPack question end-to-end.

    1. Load the question definition from QPack JSON files
    2. Run all context_queries against the SQLite database
    3. If static_answer exists, return that directly
    4. If requires_llm, assemble prompt and call Ollama
    5. Otherwise, return raw query results

    Returns a structured result dict.
    """
    start_ms = time.time()

    # Find the question
    question = _find_question(question_id, qpack_dir)
    if question is None:
        return {
            "question_id": question_id,
            "label": None,
            "answer_format": None,
            "source": None,
            "model": None,
            "context_data": {},
            "llm_response": None,
            "static_answer": None,
            "execution_ms": int((time.time() - start_ms) * 1000),
            "token_count": None,
            "error": f"Question '{question_id}' not found in {qpack_dir}",
        }

    label = question.get("label", question_id)
    answer_format = question.get("answer_format")

    # ── Static answer (onboarding questions) ──
    if "static_answer" in question:
        return {
            "question_id": question_id,
            "label": label,
            "answer_format": answer_format,
            "source": "static",
            "model": None,
            "context_data": {},
            "llm_response": None,
            "static_answer": question["static_answer"],
            "execution_ms": int((time.time() - start_ms) * 1000),
            "token_count": None,
        }

    # ── Run context queries ──
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    try:
        context_data = _run_context_queries(question, db)
    finally:
        db.close()

    # ── LLM execution path ──
    if question.get("requires_llm"):
        prompt = _assemble_prompt(question, context_data)

        if not prompt:
            # No prompt template — fall back to database-only
            return {
                "question_id": question_id,
                "label": label,
                "answer_format": answer_format,
                "source": "database",
                "model": None,
                "context_data": context_data,
                "llm_response": None,
                "static_answer": None,
                "execution_ms": int((time.time() - start_ms) * 1000),
                "token_count": None,
            }

        # Pick a machine
        machine = _pick_machine()
        if machine is None:
            # Degraded response — no LLM available
            return {
                "question_id": question_id,
                "label": label,
                "answer_format": answer_format,
                "source": "database",
                "model": None,
                "context_data": context_data,
                "llm_response": None,
                "static_answer": None,
                "execution_ms": int((time.time() - start_ms) * 1000),
                "token_count": None,
                "degraded": True,
                "degraded_reason": "No LLM machines available on the network. Returning raw data only.",
            }

        # Build system prompt from persona
        persona = question.get("_persona", {})
        system_prompt = persona.get("system_prompt")

        result = _call_ollama(
            ip=machine["ip"],
            port=machine["port"],
            model=machine["model"],
            prompt=prompt,
            system=system_prompt,
        )

        if "error" in result:
            # LLM call failed — return degraded with context data
            return {
                "question_id": question_id,
                "label": label,
                "answer_format": answer_format,
                "source": "database",
                "model": machine["model"],
                "context_data": context_data,
                "llm_response": None,
                "static_answer": None,
                "execution_ms": int((time.time() - start_ms) * 1000),
                "token_count": None,
                "degraded": True,
                "degraded_reason": f"LLM error ({machine['name']}): {result['error']}",
            }

        token_count = (result.get("tokens_in") or 0) + (result.get("tokens_out") or 0)

        return {
            "question_id": question_id,
            "label": label,
            "answer_format": answer_format,
            "source": "local_llm",
            "model": result.get("model"),
            "context_data": context_data,
            "llm_response": result.get("response", ""),
            "static_answer": None,
            "execution_ms": int((time.time() - start_ms) * 1000),
            "token_count": token_count if token_count > 0 else None,
        }

    # ── Database-only path ──
    return {
        "question_id": question_id,
        "label": label,
        "answer_format": answer_format,
        "source": "database",
        "model": None,
        "context_data": context_data,
        "llm_response": None,
        "static_answer": None,
        "execution_ms": int((time.time() - start_ms) * 1000),
        "token_count": None,
    }


# ──────────────────────────────────────────────────────────────────────
#  Pipeline step — execute specific or all featured questions
# ──────────────────────────────────────────────────────────────────────

class ExecuteStep(PipelineStep):
    """
    Pipeline step that executes QPack questions.

    If question_ids are provided, executes those. Otherwise executes
    all featured questions from the adapted templates.
    """
    name = "execute"

    def __init__(self, question_ids: list[str] = None):
        self.question_ids = question_ids

    def __call__(self, ctx: dict) -> dict:
        log = ctx["_pipeline"].log

        # Determine which questions to execute
        if self.question_ids:
            ids_to_run = self.question_ids
        else:
            # Execute all featured questions from adapted templates
            templates = ctx.get("adapted_templates", ctx.get("validated_templates", {}))
            ids_to_run = []
            for template in templates.values():
                for q in template.get("questions", []):
                    if q.get("featured"):
                        ids_to_run.append(q["id"])

        if not ids_to_run:
            log("    no questions to execute")
            ctx["execution_results"] = []
            return ctx

        log(f"    executing {len(ids_to_run)} questions")

        results = []
        for qid in ids_to_run:
            log(f"      {qid}...")
            result = execute_question(qid)
            source = result.get("source", "?")
            ms = result.get("execution_ms", 0)
            degraded = " [DEGRADED]" if result.get("degraded") else ""
            log(f"        {source} — {ms}ms{degraded}")
            results.append(result)

        succeeded = sum(1 for r in results if not r.get("error") and not r.get("degraded"))
        degraded = sum(1 for r in results if r.get("degraded"))
        failed = sum(1 for r in results if r.get("error"))
        log(f"    {succeeded} succeeded, {degraded} degraded, {failed} failed")

        ctx["execution_results"] = results
        return ctx


# ──────────────────────────────────────────────────────────────────────
#  CLI
# ──────────────────────────────────────────────────────────────────────

def _print_result(result: dict):
    """Pretty-print an execution result."""
    print(f"\n{'='*60}")
    print(f"  Question: {result.get('label', result['question_id'])}")
    print(f"  ID:       {result['question_id']}")
    print(f"  Source:   {result.get('source', '?')}")
    print(f"  Model:    {result.get('model') or '—'}")
    print(f"  Time:     {result.get('execution_ms', 0)}ms")
    if result.get('token_count'):
        print(f"  Tokens:   {result['token_count']}")
    if result.get('degraded'):
        print(f"  DEGRADED: {result.get('degraded_reason', 'unknown')}")
    if result.get('error'):
        print(f"  ERROR:    {result['error']}")
    print(f"{'='*60}")

    # Static answer
    if result.get("static_answer"):
        print(f"\n  --- Static Answer ---")
        for section, text in result["static_answer"].items():
            print(f"  [{section}] {text}")

    # Context data
    if result.get("context_data"):
        for key, rows in result["context_data"].items():
            print(f"\n  --- {key} ---")
            if isinstance(rows, list):
                if rows:
                    cols = list(rows[0].keys())
                    print(f"  {' | '.join(str(c)[:20].ljust(20) for c in cols)}")
                    print(f"  {'─' * (22 * len(cols))}")
                    for row in rows[:15]:
                        vals = [str(row.get(c, "—") if row.get(c) is not None else "—")[:20].ljust(20) for c in cols]
                        print(f"  {' | '.join(vals)}")
                    if len(rows) > 15:
                        print(f"  ... and {len(rows) - 15} more rows")
                    print(f"  [{len(rows)} rows]")
                else:
                    print(f"  (no results)")
            elif isinstance(rows, dict) and "error" in rows:
                print(f"  ERROR: {rows['error']}")

    # LLM response
    if result.get("llm_response"):
        print(f"\n  --- LLM Response ---")
        print(f"  {result['llm_response']}")

    print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        # List all available questions
        print(f"\n{'='*60}")
        print(f"  QPack Executor — Available Questions")
        print(f"{'='*60}\n")

        questions = _list_all_questions()
        if not questions:
            print("  No QPacks found. Run 'python3 modules/qpack-generator/run.py generate' first.")
        else:
            for q in questions:
                featured = " *" if q.get("featured") else "  "
                llm = " [LLM]" if q.get("requires_llm") else ""
                static = " [static]" if "static_answer" in q else ""
                print(f"  {featured} {q['id']:45s} {q.get('label', '')}{llm}{static}")
            print(f"\n  Usage: python3 steps/execute.py <question_id>")
            print(f"  * = featured\n")
        sys.exit(0)

    question_id = sys.argv[1]
    result = execute_question(question_id)
    _print_result(result)

    # Print JSON for piping
    if "--json" in sys.argv:
        print(json.dumps(result, indent=2, default=str))
