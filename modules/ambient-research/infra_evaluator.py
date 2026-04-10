#!/usr/bin/env python3
"""
Infrastructure Evaluator — Autonomous improvement recommendations for SoY.

Evaluates ambient research findings from the "SoY Infrastructure & Claude Ecosystem"
stream against the actual SoY architecture, producing scored recommendations.

Usage:
    python3 modules/ambient-research/infra_evaluator.py seed           # Create stream + tables
    python3 modules/ambient-research/infra_evaluator.py evaluate --tier 1  # Tier 1: relevance filter
    python3 modules/ambient-research/infra_evaluator.py evaluate --tier 2  # Tier 2: architecture eval
    python3 modules/ambient-research/infra_evaluator.py plan               # Tier 3: implementation plans
    python3 modules/ambient-research/infra_evaluator.py calibrate          # Recalibrate weights
    python3 modules/ambient-research/infra_evaluator.py status             # Show pipeline status
"""

import json
import hashlib
import sqlite3
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

DB_PATH = Path.home() / ".local" / "share" / "software-of-you" / "soy.db"
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
LOG_FILE = Path.home() / ".local" / "share" / "software-of-you" / "infra-evaluator.log"

STREAM_NAME = "SoY Infrastructure & Claude Ecosystem"

sys.path.insert(0, str(PLUGIN_ROOT / "shared"))
try:
    from agent_heartbeat import agent_start, agent_complete, agent_fail
except ImportError:
    def agent_start(s, m, **kw): return "noop"
    def agent_complete(s, r, m, **kw): pass
    def agent_fail(s, r, m, **kw): pass

MACHINES = {
    "soy-1": {"ip": "100.91.234.67", "port": 11434, "tier": 1},
    "legion": {"ip": "100.69.255.78", "port": 11434, "tier": 2},
    "lucy": {"ip": "100.74.238.16", "port": 11434, "tier": 2},
}

CATEGORIES = [
    "dependency_update", "config_tweak", "new_feature", "performance",
    "security", "api_migration", "model_upgrade", "prompt_improvement", "mcp_update",
]


def log(msg: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def ollama_generate(ip, port, model, prompt, system, timeout=300):
    payload = json.dumps({
        "model": model, "prompt": prompt, "stream": False,
        "options": {"temperature": 0.3}, "system": system,
    }).encode()
    req = urllib.request.Request(
        f"http://{ip}:{port}/api/generate",
        data=payload, headers={"Content-Type": "application/json"}, method="POST",
    )
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        duration = int((time.time() - start) * 1000)
        tokens_out = data.get("eval_count", 0)
        eval_rate = round(tokens_out / (data.get("eval_duration", 1) / 1e9), 1) if data.get("eval_duration") else 0
        response_text = data.get("response", "")
        if isinstance(response_text, bytes):
            response_text = response_text.decode("utf-8", errors="replace")
        else:
            response_text = response_text.encode("utf-8", errors="replace").decode("utf-8")
        return {"response": response_text, "tokens_out": tokens_out, "duration_ms": duration, "eval_rate": eval_rate}
    except Exception as e:
        return {"error": str(e)}


def check_machine(name):
    m = MACHINES[name]
    try:
        req = urllib.request.Request(f"http://{m['ip']}:{m['port']}/api/tags")
        with urllib.request.urlopen(req, timeout=5):
            return True
    except Exception:
        return False


# --- Architecture Context ---

def gather_architecture_context():
    """Read the actual SoY architecture to inject into evaluation prompts."""
    ctx = []

    # CLAUDE.md (first 3000 chars — architecture overview)
    claude_md = PLUGIN_ROOT / "CLAUDE.md"
    if claude_md.exists():
        content = claude_md.read_text()[:3000]
        ctx.append(f"## CLAUDE.md (Architecture Overview)\n{content}")

    # Installed modules
    db = get_db()
    modules = db.execute("SELECT name, version FROM modules WHERE enabled = 1 ORDER BY name").fetchall()
    if modules:
        mod_list = "\n".join(f"  - {m['name']} v{m['version']}" for m in modules)
        ctx.append(f"## Installed Modules\n{mod_list}")

    # Machine topology
    machines = db.execute("SELECT name, tailscale_ip, gpu, vram_mb, models, active FROM research_machines").fetchall()
    if machines:
        mach_list = "\n".join(
            f"  - {m['name']}: GPU={m['gpu'] or '?'}, VRAM={m['vram_mb'] or '?'}MB, "
            f"models={m['models'] or '[]'}, active={'yes' if m['active'] else 'no'}"
            for m in machines
        )
        ctx.append(f"## Machine Topology\n{mach_list}")

    # Active research streams
    streams = db.execute("SELECT name, priority FROM research_streams WHERE active = 1 ORDER BY priority DESC").fetchall()
    if streams:
        s_list = "\n".join(f"  - [{s['priority']}] {s['name']}" for s in streams)
        ctx.append(f"## Active Research Streams\n{s_list}")

    # Recent git log
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "-15"],
            capture_output=True, text=True, timeout=10, cwd=str(PLUGIN_ROOT),
        )
        if result.returncode == 0 and result.stdout.strip():
            ctx.append(f"## Recent Git History\n{result.stdout.strip()}")
    except Exception:
        pass

    # Recent session handoffs
    handoffs = db.execute(
        "SELECT summary, source, created_at FROM session_handoffs ORDER BY created_at DESC LIMIT 3"
    ).fetchall()
    if handoffs:
        h_list = "\n".join(f"  [{h['source']} @ {h['created_at']}]: {(h['summary'] or '')[:200]}" for h in handoffs)
        ctx.append(f"## Recent Session Handoffs\n{h_list}")

    db.close()

    full_ctx = "\n\n".join(ctx)
    ctx_hash = hashlib.sha256(full_ctx.encode()).hexdigest()[:16]
    return full_ctx, ctx_hash


def compute_composite(scores, db):
    """Compute weighted composite score from dimension weights."""
    weights = {
        row["dimension"]: row["weight"]
        for row in db.execute("SELECT dimension, weight FROM infra_score_weights").fetchall()
    }
    total_weight = sum(weights.values())
    if total_weight == 0:
        return 5.0
    composite = sum(
        scores.get(dim, 5) * weight
        for dim, weight in weights.items()
    ) / total_weight
    return round(composite, 2)


def get_stream_id(db):
    """Get the infra stream ID."""
    row = db.execute("SELECT id FROM research_streams WHERE name = ?", (STREAM_NAME,)).fetchone()
    return row["id"] if row else None


# --- Seed ---

def cmd_seed():
    """Create the infra research stream and seed initial state."""
    db = get_db()

    existing = db.execute("SELECT id FROM research_streams WHERE name = ?", (STREAM_NAME,)).fetchone()
    if existing:
        log(f"Stream already exists (id={existing['id']})")
        db.close()
        return

    keywords = json.dumps([
        "Claude Code releases", "Claude Code changelog", "Anthropic API updates",
        "MCP protocol", "Model Context Protocol", "Ollama releases", "Ollama changelog",
        "local LLM optimization", "GGUF quantization", "prompt engineering techniques",
        "context window optimization", "long context strategies",
        "Claude Opus", "Claude Sonnet", "Claude 4",
        "SQLite optimization", "Tailscale updates", "systemd patterns",
        "discord.py updates", "Python AI tooling",
    ])

    db.execute(
        """INSERT INTO research_streams
           (name, description, keywords, linked_project_ids, priority,
            tier_1_cadence_hours, tier_2_cadence_hours, tier_3_cadence_hours, active)
           VALUES (?, ?, ?, '[]', 9, 6, 12, 168, 1)""",
        (
            STREAM_NAME,
            "Monitors Claude Code releases, Anthropic API changes, MCP protocol updates, "
            "Ollama releases, local LLM optimization techniques, prompt engineering advances, "
            "and context window strategies relevant to the Software of You platform architecture.",
            keywords,
        ),
    )
    db.commit()
    stream_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Seed wiki
    db.execute(
        """INSERT INTO research_wikis (stream_id, title, content, version, word_count)
           VALUES (?, ?, ?, 1, 0)""",
        (stream_id, f"{STREAM_NAME} — Research Wiki", f"# {STREAM_NAME}\n\n*(Awaiting first Tier 1 findings)*"),
    )
    db.commit()
    db.close()

    log(f"Stream created: {STREAM_NAME} (id={stream_id}, priority=9)")
    log("Run `python3 modules/ambient-research/run.py tier1` to generate first findings")


# --- Tier 1: Relevance Filter ---

TIER1_SYSTEM = """You are a technical relevance filter for a personal data platform called Software of You (SoY).

SoY's tech stack: SQLite, Python 3.12, Claude Code CLI, Ollama (Mistral 7B, Qwen 14B/32B, Gemma4),
Tailscale mesh network, discord.py, systemd/cron, bash scripting, MCP protocol, Syncthing,
Docker (media stack), Node.js 22, Flask, aiohttp, Playwright.

Given a research finding, respond with ONLY a JSON object (no markdown, no explanation):
{"relevant": true, "reason": "one sentence why"}
or
{"relevant": false, "reason": "one sentence why"}

Mark as relevant if it concerns: Claude/Anthropic updates, Ollama releases, MCP protocol changes,
local LLM optimization, SQLite improvements, Python tooling updates, Tailscale networking,
systemd/cron patterns, prompt engineering, context window techniques, Docker best practices,
or anything that could improve a multi-machine AI-powered personal data platform.

Mark as irrelevant if it's: general AI hype with no concrete tooling changes, enterprise-only
features, cloud-only services with no local alternative, unrelated languages/frameworks."""


def evaluate_tier1():
    """Binary relevance filter on unprocessed findings from the infra stream."""
    if not check_machine("soy-1"):
        log("soy-1 offline — skipping Tier 1 evaluation")
        return

    db = get_db()
    stream_id = get_stream_id(db)
    if not stream_id:
        log("Infra stream not found — run 'seed' first")
        db.close()
        return

    # Find findings that haven't been evaluated yet
    findings = db.execute("""
        SELECT f.id, f.content, f.title
        FROM research_findings f
        WHERE f.stream_id = ?
          AND f.id NOT IN (SELECT finding_id FROM infra_recommendations WHERE finding_id IS NOT NULL)
        ORDER BY f.created_at DESC
        LIMIT 20
    """, (stream_id,)).fetchall()

    if not findings:
        log("No new findings to evaluate")
        db.close()
        return

    log(f"=== Infra Evaluator — Tier 1 Filter ({len(findings)} findings) ===")
    m = MACHINES["soy-1"]
    relevant_count = 0

    for f in findings:
        result = ollama_generate(m["ip"], m["port"], "mistral:7b", f["content"][:2000], TIER1_SYSTEM, timeout=60)

        if "error" in result:
            log(f"  Finding {f['id']}: ERROR — {result['error'][:80]}")
            continue

        try:
            response_text = result["response"].strip()
            # Try to extract JSON from response
            if "{" in response_text:
                json_str = response_text[response_text.index("{"):response_text.rindex("}") + 1]
                verdict = json.loads(json_str)
            else:
                verdict = {"relevant": False, "reason": "Could not parse response"}
        except (json.JSONDecodeError, ValueError):
            verdict = {"relevant": False, "reason": "Could not parse response"}

        if verdict.get("relevant"):
            db.execute("""
                INSERT INTO infra_recommendations
                    (finding_id, stream_id, category, title, description, status, tier_evaluated, model_used)
                VALUES (?, ?, 'pending_classification', ?, ?, 'pending', 1, 'mistral:7b')
            """, (f["id"], stream_id, f["title"] or "Unclassified finding", verdict.get("reason", "")))
            db.commit()
            relevant_count += 1
            log(f"  Finding {f['id']}: RELEVANT — {verdict.get('reason', '')[:80]}")
        else:
            log(f"  Finding {f['id']}: filtered — {verdict.get('reason', '')[:80]}")

    db.close()
    log(f"=== Tier 1 Filter Complete: {relevant_count}/{len(findings)} relevant ===")


# --- Tier 2: Architecture Evaluation ---

TIER2_SYSTEM_TEMPLATE = """You are an infrastructure improvement evaluator for Software of You (SoY), a personal data platform.
You evaluate research findings against the actual SoY architecture and produce scored, actionable recommendations.

## SoY Architecture Context
{architecture_context}

## Scoring Dimensions (1-10 each)
- relevance: How directly does this apply to SoY's actual codebase and architecture?
- effort: How easy to implement? (10=trivial config change, 1=massive rewrite)
- impact: How much would this improve SoY? (10=transformative, 1=negligible)
- urgency: Time sensitivity? (10=breaking change imminent, 1=nice-to-have someday)
- risk: Risk of NOT doing this? (10=security vulnerability, 1=zero consequence)

## Categories
dependency_update, config_tweak, new_feature, performance, security, api_migration, model_upgrade, prompt_improvement, mcp_update

## Auto-eligibility
Mark auto_eligible=true ONLY for: version bumps in configs, Ollama model pulls, cron schedule adjustments, environment variable changes. Everything else requires human review.

Respond with ONLY a JSON object (no markdown fences, no explanation):
{{
  "title": "Update X in file Y because Z",
  "description": "Full rationale with evidence from the finding",
  "category": "one of the categories above",
  "relevance": N, "effort": N, "impact": N, "urgency": N, "risk": N,
  "target_files": ["path/to/file1.py"],
  "proposed_changes": "## What to change\\nConcrete description of the change",
  "affected_modules": ["module-name"],
  "auto_eligible": false,
  "requires_review": "Why human review is needed"
}}

Be CONCRETE. Not "consider improving performance" but "Add WAL mode to X file because Y."
If the finding doesn't warrant a specific recommendation, respond: {{"skip": true, "reason": "why"}}"""


def evaluate_tier2():
    """Multi-dimension scoring with architecture context injection."""
    # Prefer Legion (gemma4:e2b @ 164 tok/s), fall back to Lucy, then Razer with 7b
    if check_machine("legion"):
        machine_name, model = "legion", "gemma4:e2b"
    elif check_machine("lucy"):
        machine_name, model = "lucy", "qwen2.5:14b"
    elif check_machine("soy-1"):
        machine_name, model = "soy-1", "qwen2.5:7b"
    else:
        log("No machines available — skipping Tier 2 evaluation")
        return

    db = get_db()

    # Find Tier 1 filtered recommendations that haven't been scored yet
    pending = db.execute("""
        SELECT r.id, r.finding_id, r.title, f.content
        FROM infra_recommendations r
        JOIN research_findings f ON f.id = r.finding_id
        WHERE r.tier_evaluated = 1 AND r.status = 'pending'
        ORDER BY r.created_at ASC
        LIMIT 10
    """).fetchall()

    if not pending:
        log("No pending Tier 1 recommendations to evaluate")
        db.close()
        return

    arch_context, arch_hash = gather_architecture_context()
    system_prompt = TIER2_SYSTEM_TEMPLATE.format(architecture_context=arch_context)

    log(f"=== Infra Evaluator — Tier 2 Scoring ({len(pending)} recommendations, {machine_name}) ===")
    m = MACHINES[machine_name]
    scored = 0

    for rec in pending:
        result = ollama_generate(m["ip"], m["port"], model, rec["content"][:3000], system_prompt, timeout=120)

        if "error" in result:
            log(f"  Rec {rec['id']}: ERROR — {result['error'][:80]}")
            continue

        try:
            response_text = result["response"].strip()
            if "{" in response_text:
                json_str = response_text[response_text.index("{"):response_text.rindex("}") + 1]
                evaluation = json.loads(json_str)
            else:
                log(f"  Rec {rec['id']}: no JSON in response")
                continue
        except (json.JSONDecodeError, ValueError) as e:
            log(f"  Rec {rec['id']}: JSON parse error — {e}")
            continue

        if evaluation.get("skip"):
            db.execute("UPDATE infra_recommendations SET status = 'rejected', review_notes = ?, tier_evaluated = 2, updated_at = datetime('now') WHERE id = ?",
                (evaluation.get("reason", "Skipped by Tier 2"), rec["id"]))
            db.commit()
            log(f"  Rec {rec['id']}: SKIPPED — {evaluation.get('reason', '')[:80]}")
            continue

        scores = {
            "relevance": evaluation.get("relevance", 5),
            "effort": evaluation.get("effort", 5),
            "impact": evaluation.get("impact", 5),
            "urgency": evaluation.get("urgency", 5),
            "risk": evaluation.get("risk", 5),
        }
        composite = compute_composite(scores, db)
        category = evaluation.get("category", "config_tweak")
        if category not in CATEGORIES:
            category = "config_tweak"

        auto_eligible = 1 if evaluation.get("auto_eligible") else 0

        db.execute("""
            UPDATE infra_recommendations SET
                title = ?, description = ?, category = ?,
                relevance_score = ?, effort_score = ?, impact_score = ?,
                urgency_score = ?, risk_score = ?, composite_score = ?,
                target_files = ?, proposed_changes = ?, affected_modules = ?,
                auto_eligible = ?, requires_review = ?,
                tier_evaluated = 2, model_used = ?, architecture_hash = ?,
                updated_at = datetime('now')
            WHERE id = ?
        """, (
            evaluation.get("title", rec["title"]),
            evaluation.get("description", ""),
            category,
            scores["relevance"], scores["effort"], scores["impact"],
            scores["urgency"], scores["risk"], composite,
            json.dumps(evaluation.get("target_files", [])),
            evaluation.get("proposed_changes", ""),
            json.dumps(evaluation.get("affected_modules", [])),
            auto_eligible,
            evaluation.get("requires_review"),
            model,
            arch_hash,
            rec["id"],
        ))

        # Auto-approve if eligible
        if auto_eligible:
            db.execute("UPDATE infra_recommendations SET status = 'approved', reviewed_by = 'auto', reviewed_at = datetime('now') WHERE id = ?", (rec["id"],))
            log(f"  Rec {rec['id']}: AUTO-APPROVED — {evaluation.get('title', '')[:60]} (composite={composite})")
        else:
            log(f"  Rec {rec['id']}: SCORED — {evaluation.get('title', '')[:60]} (composite={composite})")

        db.commit()
        scored += 1

    db.close()
    log(f"=== Tier 2 Scoring Complete: {scored}/{len(pending)} scored ===")


# --- Tier 3: Implementation Planning ---

def cmd_plan():
    """Generate implementation plans for approved recommendations via Claude CLI."""
    db = get_db()

    approved = db.execute("""
        SELECT r.*, f.content as finding_content
        FROM infra_recommendations r
        LEFT JOIN research_findings f ON f.id = r.finding_id
        WHERE r.status = 'approved' AND r.handoff_id IS NULL
        ORDER BY r.composite_score DESC
        LIMIT 5
    """).fetchall()

    if not approved:
        log("No approved recommendations awaiting implementation plans")
        db.close()
        return

    log(f"=== Infra Evaluator — Tier 3 Planning ({len(approved)} recommendations) ===")

    # Find claude binary
    claude_bin = None
    for path in ["/usr/local/bin/claude", "/usr/bin/claude"]:
        if Path(path).exists():
            claude_bin = path
            break
    if not claude_bin:
        try:
            result = subprocess.run(["which", "claude"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                claude_bin = result.stdout.strip()
        except Exception:
            pass

    if not claude_bin:
        log("Claude CLI not found — skipping Tier 3")
        db.close()
        return

    for rec in approved:
        # Read target files for context
        file_contents = []
        target_files = json.loads(rec["target_files"]) if rec["target_files"] else []
        for tf in target_files[:5]:
            fp = PLUGIN_ROOT / tf
            if fp.exists():
                try:
                    content = fp.read_text()[:5000]
                    file_contents.append(f"### {tf}\n```\n{content}\n```")
                except Exception:
                    pass

        prompt = f"""You are implementing an infrastructure improvement for Software of You (SoY).

## Recommendation
Title: {rec['title']}
Category: {rec['category']}
Description: {rec['description']}
Composite Score: {rec['composite_score']}

## Proposed Changes
{rec['proposed_changes']}

## Target Files
{chr(10).join(file_contents) if file_contents else 'No target files specified'}

## Task
Produce an implementation plan with:
1. Exact code changes (show diffs or complete replacement blocks)
2. Any new migration SQL needed
3. Test plan (how to verify the change works)
4. Rollback procedure (how to undo if something breaks)

Be concrete and complete. The next autonomous session will execute this plan."""

        try:
            env = dict(__import__("os").environ)
            env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
            proc = subprocess.run(
                [claude_bin, "-p", prompt],
                capture_output=True, text=True, timeout=300, cwd=str(PLUGIN_ROOT), env=env,
            )
            plan_content = proc.stdout.strip() if proc.returncode == 0 else None
        except Exception as e:
            log(f"  Rec {rec['id']}: Claude CLI error — {e}")
            continue

        if not plan_content or len(plan_content) < 50:
            log(f"  Rec {rec['id']}: empty plan — skipping")
            continue

        # Create session handoff
        safe_summary = plan_content.replace("'", "''")
        db.execute("INSERT INTO session_handoffs (summary, source, status, created_at) VALUES (?, 'infra-evaluator', 'active', datetime('now'))",
            (plan_content,))
        db.commit()
        handoff_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

        db.execute("UPDATE infra_recommendations SET handoff_id = ?, status = 'implementing', updated_at = datetime('now') WHERE id = ?",
            (handoff_id, rec["id"]))
        db.commit()

        log(f"  Rec {rec['id']}: plan created ({len(plan_content)} chars) → handoff {handoff_id}")

    db.close()
    log("=== Tier 3 Planning Complete ===")


# --- Calibration ---

def cmd_calibrate():
    """Recalibrate composite weights from approval/rejection data."""
    db = get_db()
    weights = db.execute("SELECT * FROM infra_score_weights").fetchall()

    log("=== Infra Evaluator — Calibration ===")
    adjustments = 0

    for w in weights:
        dim = w["dimension"]

        approved = db.execute("""
            SELECT AVG(c.model_score) as avg_score, COUNT(*) as n
            FROM infra_calibration c
            WHERE c.dimension = ? AND c.human_verdict = 'approved'
        """, (dim,)).fetchone()

        rejected = db.execute("""
            SELECT AVG(c.model_score) as avg_score, COUNT(*) as n
            FROM infra_calibration c
            WHERE c.dimension = ? AND c.human_verdict = 'rejected'
        """, (dim,)).fetchone()

        total_samples = (approved["n"] or 0) + (rejected["n"] or 0)
        if total_samples < 10:
            log(f"  {dim}: {total_samples} samples (need 10) — skipping")
            continue

        approved_avg = approved["avg_score"] or 5
        rejected_avg = rejected["avg_score"] or 5
        separation = approved_avg - rejected_avg

        adjustment = max(-0.2, min(0.2, separation / 10))
        new_weight = max(0.5, min(3.0, w["weight"] + adjustment))

        if abs(new_weight - w["weight"]) > 0.01:
            old_weight = w["weight"]
            db.execute("""
                UPDATE infra_score_weights
                SET weight = ?, approved_avg = ?, rejected_avg = ?,
                    sample_count = ?, last_calibrated_at = datetime('now'),
                    updated_at = datetime('now')
                WHERE dimension = ?
            """, (new_weight, approved_avg, rejected_avg, total_samples, dim))

            db.execute("""
                INSERT INTO infra_evolution_log (change_type, description, old_value, new_value, reason)
                VALUES ('weight_adjusted', ?, ?, ?, ?)
            """, (
                f"Adjusted {dim} weight",
                str(old_weight),
                str(new_weight),
                f"approved_avg={approved_avg:.1f}, rejected_avg={rejected_avg:.1f}, "
                f"separation={separation:.1f}, samples={total_samples}",
            ))
            adjustments += 1
            log(f"  {dim}: {old_weight:.2f} → {new_weight:.2f} (sep={separation:.1f}, n={total_samples})")
        else:
            log(f"  {dim}: {w['weight']:.2f} — no change needed (sep={separation:.1f}, n={total_samples})")

    db.commit()
    db.close()
    log(f"=== Calibration Complete: {adjustments} adjustments ===")


# --- Status ---

def cmd_status():
    db = get_db()
    stream_id = get_stream_id(db)

    print(f"\n{'='*60}")
    print(f"  INFRASTRUCTURE ADVISOR — Pipeline Status")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}\n")

    if not stream_id:
        print("  Stream not created — run 'seed' first")
        db.close()
        return

    # Stream info
    stream = db.execute("SELECT * FROM research_streams WHERE id = ?", (stream_id,)).fetchone()
    print(f"  Stream: {stream['name']} (priority={stream['priority']})")

    # Findings count
    fc = db.execute("SELECT COUNT(*) as n FROM research_findings WHERE stream_id = ?", (stream_id,)).fetchone()
    print(f"  Findings: {fc['n']}")

    # Recommendation counts by status
    counts = db.execute("""
        SELECT status, COUNT(*) as n FROM infra_recommendations
        WHERE stream_id = ? GROUP BY status ORDER BY status
    """, (stream_id,)).fetchall()
    if counts:
        print(f"\n  Recommendations:")
        for c in counts:
            print(f"    {c['status']}: {c['n']}")
    else:
        print(f"\n  No recommendations yet")

    # Top pending
    pending = db.execute("""
        SELECT title, composite_score, category
        FROM infra_recommendations
        WHERE stream_id = ? AND status = 'pending' AND tier_evaluated = 2
        ORDER BY composite_score DESC LIMIT 5
    """, (stream_id,)).fetchall()
    if pending:
        print(f"\n  Top Pending:")
        for p in pending:
            print(f"    [{p['composite_score']:.1f}] [{p['category']}] {p['title'][:60]}")

    # Score weights
    weights = db.execute("SELECT dimension, weight, sample_count FROM infra_score_weights ORDER BY weight DESC").fetchall()
    print(f"\n  Score Weights:")
    for w in weights:
        print(f"    {w['dimension']}: {w['weight']:.2f} (n={w['sample_count']})")

    # Evolution log
    evo = db.execute("SELECT change_type, description, created_at FROM infra_evolution_log ORDER BY created_at DESC LIMIT 3").fetchall()
    if evo:
        print(f"\n  Recent Evolution:")
        for e in evo:
            print(f"    [{e['created_at']}] {e['change_type']}: {e['description'][:60]}")

    print()
    db.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 infra_evaluator.py [seed|evaluate|plan|calibrate|status]")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "seed":
        cmd_seed()
    elif cmd == "evaluate":
        tier = 1
        if "--tier" in sys.argv:
            idx = sys.argv.index("--tier")
            if idx + 1 < len(sys.argv):
                tier = int(sys.argv[idx + 1])
        if tier == 1:
            evaluate_tier1()
        elif tier == 2:
            evaluate_tier2()
        else:
            print(f"Unknown tier: {tier}")
    elif cmd == "plan":
        cmd_plan()
    elif cmd == "calibrate":
        cmd_calibrate()
    elif cmd == "status":
        cmd_status()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
