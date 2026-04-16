"""
report.py — Markdown report generator for the loci benchmark.

Reads judge_scores from results.db, unscrambles the blind labels back to
(A, B, C), computes per-arm and per-bucket averages, and writes a
markdown file with:

  - Headline numbers (per-arm aggregates across all dimensions)
  - Per-bucket breakdown (recall, prep, connection, synthesis, adversarial)
  - Notable findings (large gradients, ties, hallucinations, low confidence)
  - Per-prompt detail with all three arms side by side, including the
    judge's one-liner-right / one-liner-missed and the rationale fields
    that the user wanted to panel-review

The reporting layer is intentionally separate from the judge so a bad
run can be re-reported without re-judging. The judge_scores table is
the source of truth.

Conventions:
- Pure stdlib (sqlite3, json, statistics, datetime).
- Output goes to benchmarks/loci/report-<run_id>.md (gitignored — reports
  are regenerable from results.db).

Usage (as a module):
    from report import generate_report
    path = generate_report(run_id="20260410_141453")

Or via runner.py:
    python3 benchmarks/loci/runner.py report <run_id>
"""

import json
import os
import sqlite3
import statistics
import sys
from datetime import datetime


# ─── Helpers ─────────────────────────────────────────────────────────

def _open_results(results_db_path: str) -> sqlite3.Connection:
    if not os.path.exists(results_db_path):
        raise FileNotFoundError(f"results database not found at {results_db_path}")
    conn = sqlite3.connect(results_db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _load_prompts_by_id(prompts_path: str) -> dict:
    with open(prompts_path) as f:
        data = json.load(f)
    return {p["id"]: p for p in data["prompts"]}


def _mean(values: list) -> float:
    """Mean of a list, returns 0.0 if empty (so the report doesn't crash on partial data)."""
    if not values:
        return 0.0
    return statistics.mean(values)


def _fmt_score(value, decimals: int = 2) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{decimals}f}"
    return str(value)


def _arm_label(arm_id: str) -> str:
    return {
        "A": "A — flat-only",
        "B": "B — SoY-as-it-is",
        "C": "C — loci layer",
    }.get(arm_id, arm_id)


# ─── Data loading ────────────────────────────────────────────────────

def _load_run_data(db: sqlite3.Connection, run_id: str) -> dict:
    """Pull all rows for a run from runs, arm_results, judge_scores.
    Tolerates judge_scores not existing yet (e.g. report run before judge)."""
    run_row = db.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if not run_row:
        raise ValueError(f"no run found with id {run_id}")

    arm_rows = db.execute(
        "SELECT * FROM arm_results WHERE run_id = ? ORDER BY prompt_id, arm_id",
        (run_id,),
    ).fetchall()

    try:
        judge_rows = db.execute(
            "SELECT * FROM judge_scores WHERE run_id = ? ORDER BY prompt_id, arm_id",
            (run_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        # judge_scores table doesn't exist — judging hasn't been run yet
        judge_rows = []

    # Convert all rows to dicts for consistent .get() access downstream
    arm_dicts = [dict(r) for r in arm_rows]
    judge_dicts = [dict(r) for r in judge_rows]

    arms_by_pa = {(r["prompt_id"], r["arm_id"]): r for r in arm_dicts}
    judges_by_pa = {(r["prompt_id"], r["arm_id"]): r for r in judge_dicts}

    return {
        "run": dict(run_row),
        "arm_rows": arm_dicts,
        "judge_rows": judge_dicts,
        "arms_by_pa": arms_by_pa,
        "judges_by_pa": judges_by_pa,
    }


# ─── Aggregations ────────────────────────────────────────────────────

_NUMERIC_DIMENSIONS = [
    "relevance", "completeness", "surfaced_non_obvious",
    "hallucination_count", "judge_confidence",
]


def _per_arm_stats(judge_rows: list, arm_rows: list) -> dict:
    """Compute mean of each numeric dimension per arm. Returns {arm_id: {dim: mean}}."""
    result: dict = {}
    by_arm_judge: dict = {}
    for r in judge_rows:
        if r.get("error"):
            continue
        by_arm_judge.setdefault(r["arm_id"], []).append(r)
    by_arm_arm: dict = {}
    for r in arm_rows:
        by_arm_arm.setdefault(r["arm_id"], []).append(r)

    for arm_id in sorted(set(list(by_arm_judge.keys()) + list(by_arm_arm.keys()))):
        judges = by_arm_judge.get(arm_id, [])
        arms = by_arm_arm.get(arm_id, [])
        stats = {}
        for dim in _NUMERIC_DIMENSIONS:
            stats[dim] = _mean([r[dim] for r in judges if r.get(dim) is not None])
        stats["mean_context_chars"] = _mean([r["context_chars"] for r in arms])
        stats["mean_assembly_ms"] = _mean(
            [r["assembly_elapsed_ms"] for r in arms if r.get("assembly_elapsed_ms")]
        )
        stats["mean_answer_ms"] = _mean(
            [r["answer_elapsed_ms"] for r in arms if r.get("answer_elapsed_ms")]
        )
        stats["n_judged"] = len(judges)
        stats["n_runs"] = len(arms)
        result[arm_id] = stats
    return result


def _per_bucket_stats(judge_rows: list, prompts_by_id: dict) -> dict:
    """Per-bucket × per-arm averages for the numeric dimensions."""
    by_bucket_arm: dict = {}
    for r in judge_rows:
        if r.get("error"):
            continue
        bucket = prompts_by_id.get(r["prompt_id"], {}).get("bucket", "unknown")
        by_bucket_arm.setdefault(bucket, {}).setdefault(r["arm_id"], []).append(r)

    result: dict = {}
    for bucket, arm_map in by_bucket_arm.items():
        result[bucket] = {}
        for arm_id, rows in arm_map.items():
            stats = {}
            for dim in _NUMERIC_DIMENSIONS:
                stats[dim] = _mean([r[dim] for r in rows if r.get(dim) is not None])
            stats["n"] = len(rows)
            result[bucket][arm_id] = stats
    return result


def _notable_findings(judge_rows: list, arm_rows: list, prompts_by_id: dict) -> dict:
    """Identify prompts worth flagging in the report."""
    # Build a {prompt_id: {arm_id: judge_row}} index
    by_prompt: dict = {}
    for r in judge_rows:
        if r.get("error"):
            continue
        by_prompt.setdefault(r["prompt_id"], {})[r["arm_id"]] = r

    large_gradient = []   # rel delta >= 2 between best and worst arm
    medium_gradient = []  # rel delta == 1
    ties = []             # all arms exactly equal on relevance
    hallucinated = []     # any arm has hallucination_count >= 1
    low_confidence = []   # any arm has judge_confidence <= 2

    for prompt_id, arm_map in by_prompt.items():
        if len(arm_map) < 2:
            continue
        rels = [r["relevance"] for r in arm_map.values() if r.get("relevance") is not None]
        if not rels:
            continue
        delta = max(rels) - min(rels)
        if delta >= 2:
            large_gradient.append(prompt_id)
        elif delta == 1:
            medium_gradient.append(prompt_id)
        elif delta == 0 and len(rels) == 3:
            ties.append(prompt_id)

        for r in arm_map.values():
            if r.get("hallucination_count") and r["hallucination_count"] >= 1:
                hallucinated.append((prompt_id, r["arm_id"], r["hallucination_count"]))
            if r.get("judge_confidence") is not None and r["judge_confidence"] <= 2:
                low_confidence.append((prompt_id, r["arm_id"], r["judge_confidence"]))

    return {
        "large_gradient": sorted(set(large_gradient)),
        "medium_gradient": sorted(set(medium_gradient)),
        "ties": sorted(set(ties)),
        "hallucinated": sorted(set(hallucinated)),
        "low_confidence": sorted(set(low_confidence)),
    }


# ─── Markdown rendering ──────────────────────────────────────────────

def _render_headline_table(per_arm: dict) -> str:
    headers = [
        "Arm", "Relevance", "Completeness", "Surfaced", "Hallucinations",
        "Confidence", "Context chars", "Answer ms", "n",
    ]
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join(["---"] * len(headers)) + "|"]
    for arm_id in sorted(per_arm.keys()):
        s = per_arm[arm_id]
        row = [
            _arm_label(arm_id),
            _fmt_score(s["relevance"]),
            _fmt_score(s["completeness"]),
            _fmt_score(s["surfaced_non_obvious"]),
            _fmt_score(s["hallucination_count"]),
            _fmt_score(s["judge_confidence"]),
            _fmt_score(s["mean_context_chars"], decimals=0),
            _fmt_score(s["mean_answer_ms"], decimals=0),
            str(s["n_judged"]),
        ]
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _render_bucket_section(bucket_name: str, per_bucket: dict) -> str:
    if bucket_name not in per_bucket:
        return ""
    headers = ["Arm", "Relevance", "Completeness", "Surfaced", "Hallucinations", "Confidence", "n"]
    lines = [f"### {bucket_name.capitalize()}",
             "| " + " | ".join(headers) + " |",
             "|" + "|".join(["---"] * len(headers)) + "|"]
    arm_map = per_bucket[bucket_name]
    for arm_id in sorted(arm_map.keys()):
        s = arm_map[arm_id]
        lines.append("| " + " | ".join([
            _arm_label(arm_id),
            _fmt_score(s["relevance"]),
            _fmt_score(s["completeness"]),
            _fmt_score(s["surfaced_non_obvious"]),
            _fmt_score(s["hallucination_count"]),
            _fmt_score(s["judge_confidence"]),
            str(s["n"]),
        ]) + " |")
    return "\n".join(lines)


def _render_findings_section(findings: dict) -> str:
    parts = ["## Notable findings\n"]

    def _list_or_none(label: str, items: list) -> None:
        if items:
            parts.append(f"**{label}** ({len(items)}): " + ", ".join(map(str, items)))
        else:
            parts.append(f"**{label}**: none")

    _list_or_none("Large gradient prompts (relevance Δ ≥ 2)", findings["large_gradient"])
    _list_or_none("Medium gradient prompts (relevance Δ = 1)", findings["medium_gradient"])
    _list_or_none("Tied prompts (all arms equal on relevance)", findings["ties"])

    if findings["hallucinated"]:
        parts.append(f"\n**Prompts where any arm hallucinated** "
                     f"({len(findings['hallucinated'])}):")
        for pid, arm, count in findings["hallucinated"]:
            parts.append(f"- {pid} arm {arm}: {count} hallucinations")
    else:
        parts.append("\n**Hallucinations**: zero across all arms (good)")

    if findings["low_confidence"]:
        parts.append(f"\n**Low judge confidence (≤2)** "
                     f"({len(findings['low_confidence'])}):")
        for pid, arm, conf in findings["low_confidence"]:
            parts.append(f"- {pid} arm {arm}: confidence {conf}")
    else:
        parts.append("\n**Low judge confidence**: none — judge was confident on every scored answer")

    return "\n".join(parts)


def _render_prompt_detail(prompt_id: str, prompts_by_id: dict,
                          arms_by_pa: dict, judges_by_pa: dict) -> str:
    p = prompts_by_id.get(prompt_id)
    if not p:
        return f"### {prompt_id} (not in prompts.json)\n"

    parts = [f"### {prompt_id} — {p['bucket']} — {p['prompt']}\n"]
    parts.append(f"**Gold facts:**")
    for f in p["gold"].get("facts", []):
        parts.append(f"- {f}")
    parts.append("")

    # Score table
    headers = ["Arm", "Rel", "Comp", "Surf", "Hall", "Conf", "Ctx chars", "Ans ms"]
    parts.append("| " + " | ".join(headers) + " |")
    parts.append("|" + "|".join(["---"] * len(headers)) + "|")
    for arm_id in ["A", "B", "C"]:
        arm_row = arms_by_pa.get((prompt_id, arm_id))
        judge_row = judges_by_pa.get((prompt_id, arm_id))
        if not arm_row:
            parts.append(f"| {arm_id} | — | — | — | — | — | — | — |")
            continue
        rel = comp = surf = hall = conf = "—"
        if judge_row and not judge_row.get("error"):
            rel = str(judge_row.get("relevance", "—"))
            comp = str(judge_row.get("completeness", "—"))
            surf = str(judge_row.get("surfaced_non_obvious", "—"))
            hall = str(judge_row.get("hallucination_count", "—"))
            conf = str(judge_row.get("judge_confidence", "—"))
        parts.append("| " + " | ".join([
            arm_id, rel, comp, surf, hall, conf,
            str(arm_row["context_chars"]),
            str(arm_row.get("answer_elapsed_ms") or "—"),
        ]) + " |")

    parts.append("")

    # Per-arm detail
    for arm_id in ["A", "B", "C"]:
        arm_row = arms_by_pa.get((prompt_id, arm_id))
        judge_row = judges_by_pa.get((prompt_id, arm_id))
        if not arm_row:
            continue
        parts.append(f"#### Arm {arm_id} — {_arm_label(arm_id)}")
        if arm_row.get("error"):
            parts.append(f"_Assembly error: {arm_row['error']}_\n")
            continue
        if not arm_row.get("answer"):
            parts.append("_(dry run, no answer)_\n")
            continue

        # The model's answer (truncated)
        ans = arm_row["answer"]
        if len(ans) > 800:
            ans = ans[:800] + "…"
        parts.append(f"**Answer:**\n> {ans}\n")

        if judge_row and not judge_row.get("error"):
            if judge_row.get("one_liner_right"):
                parts.append(f"**Got right:** {judge_row['one_liner_right']}")
            if judge_row.get("one_liner_missed"):
                parts.append(f"**Missed:** {judge_row['one_liner_missed']}")
            if judge_row.get("rationale_completeness"):
                parts.append(f"_Completeness rationale:_ {judge_row['rationale_completeness']}")
            if judge_row.get("rationale_hallucinations") and judge_row.get("hallucination_count", 0) > 0:
                parts.append(f"_Hallucination notes:_ {judge_row['rationale_hallucinations']}")
        elif judge_row and judge_row.get("error"):
            parts.append(f"_Judge error: {judge_row['error']}_")
        else:
            parts.append("_(not judged yet)_")
        parts.append("")

    return "\n".join(parts)


# ─── Main entry ──────────────────────────────────────────────────────

def generate_report(
    run_id: str,
    results_db_path: str,
    prompts_path: str,
    output_path: str = None,
) -> str:
    """Generate the markdown report. Returns the path the report was written to."""
    db = _open_results(results_db_path)
    try:
        data = _load_run_data(db, run_id)
    finally:
        db.close()

    prompts_by_id = _load_prompts_by_id(prompts_path)

    per_arm = _per_arm_stats(data["judge_rows"], data["arm_rows"])
    per_bucket = _per_bucket_stats(data["judge_rows"], prompts_by_id)
    findings = _notable_findings(data["judge_rows"], data["arm_rows"], prompts_by_id)

    run = data["run"]
    sections = []

    # Header
    sections.append(f"# Loci Layer Benchmark — Run {run_id}\n")
    sections.append(f"**Test model:** `{run['test_model']}` on `{run['test_host']}`")
    sections.append(f"**Judge model:** `{run.get('judge_model') or '(not yet judged)'}`")
    sections.append(f"**SoY DB:** `{run['soy_db_path']}`")
    sections.append(f"**Started:** {run['started_at']}")
    sections.append(f"**Completed:** {run.get('completed_at') or '(in progress)'}")
    sections.append(f"**Dry run:** {'yes' if run['dry_run'] else 'no'}")
    if run.get("notes"):
        sections.append(f"**Notes:** {run['notes']}")
    sections.append(f"\nReport generated at {datetime.now().isoformat(timespec='seconds')}\n")

    # Headline numbers
    sections.append("---\n\n## Headline numbers\n")
    if per_arm:
        sections.append(_render_headline_table(per_arm))
    else:
        sections.append("_No judged rows available — was this a dry run?_")
    sections.append("")

    # Per-bucket
    sections.append("---\n\n## Per-bucket breakdown\n")
    if per_bucket:
        bucket_order = ["recall", "prep", "connection", "synthesis", "adversarial"]
        for bucket in bucket_order:
            block = _render_bucket_section(bucket, per_bucket)
            if block:
                sections.append(block + "\n")
    else:
        sections.append("_No judged rows available._\n")

    # Notable findings
    sections.append("---\n")
    sections.append(_render_findings_section(findings))
    sections.append("")

    # Per-prompt detail
    sections.append("\n---\n\n## Per-prompt detail\n")
    bucket_order = ["recall", "prep", "connection", "synthesis", "adversarial"]
    by_bucket: dict = {}
    for pid in prompts_by_id:
        bucket = prompts_by_id[pid]["bucket"]
        by_bucket.setdefault(bucket, []).append(pid)

    for bucket in bucket_order:
        for pid in sorted(by_bucket.get(bucket, [])):
            sections.append(_render_prompt_detail(
                pid, prompts_by_id, data["arms_by_pa"], data["judges_by_pa"]
            ))

    body = "\n".join(sections)

    if output_path is None:
        bench_dir = os.path.dirname(results_db_path)
        output_path = os.path.join(bench_dir, f"report-{run_id}.md")

    with open(output_path, "w") as f:
        f.write(body)

    return output_path
