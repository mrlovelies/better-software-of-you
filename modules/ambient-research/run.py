#!/usr/bin/env python3
"""
Ambient Research Runner
Single entry point for cron on each machine.

Usage:
    python3 modules/ambient-research/run.py tier1        # Razer: every 6h
    python3 modules/ambient-research/run.py tier2        # Lucy: every 12h
    python3 modules/ambient-research/run.py tier3        # Any machine: overnight, Claude CLI
    python3 modules/ambient-research/run.py status       # Show pipeline status
    python3 modules/ambient-research/run.py network      # Check Ollama network health
"""

import json
import sqlite3
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path.home() / ".local" / "share" / "software-of-you" / "soy.db"
PLUGIN_ROOT = Path(__file__).resolve().parents[2]

# Agent heartbeat integration
sys.path.insert(0, str(PLUGIN_ROOT / "shared"))
try:
    from agent_heartbeat import agent_start, agent_complete, agent_fail
except ImportError:
    # Graceful fallback if heartbeat module not available
    def agent_start(s, m, **kw): return "noop"
    def agent_complete(s, r, m, **kw): pass
    def agent_fail(s, r, m, **kw): pass

MACHINES = {
    "soy-1": {"ip": "100.91.234.67", "port": 11434, "tier": 1},
    "legion": {"ip": "legion", "port": 11434, "tier": 2},
    "lucy": {"ip": "100.74.238.16", "port": 11434, "tier": 2},
}

import socket as _socket
_HOSTNAME = _socket.gethostname().lower().split(".")[0]
LOG_FILE = Path.home() / ".local" / "share" / "software-of-you" / f"ambient-research-{_HOSTNAME}.log"


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


def ollama_generate(ip: str, port: int, model: str, prompt: str, system: str, timeout: float = 300) -> dict:
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
        # Sanitize response to valid UTF-8 — Ollama can return stray bytes
        response_text = data.get("response", "")
        if isinstance(response_text, bytes):
            response_text = response_text.decode("utf-8", errors="replace")
        else:
            response_text = response_text.encode("utf-8", errors="replace").decode("utf-8")
        return {"response": response_text, "tokens_out": tokens_out, "duration_ms": duration, "eval_rate": eval_rate}
    except Exception as e:
        return {"error": str(e)}


def check_machine(name: str) -> bool:
    m = MACHINES[name]
    try:
        req = urllib.request.Request(f"http://{m['ip']}:{m['port']}/api/tags")
        with urllib.request.urlopen(req, timeout=5):
            return True
    except Exception:
        return False


SYSTEM_PROMPTS = {
    "web_sweep": "You are a research assistant. Analyze the topic and provide a structured summary of the current landscape, recent developments, key tools, and emerging trends. Be specific and cite concrete examples. Focus on actionable insights.",
    "wiki_update": "You are a wiki editor. Given the current wiki document and new findings, produce an updated version that integrates new information naturally. Don't just append — restructure and refine. The document should read as a coherent, current reference.",
}


# --- Tier 1: Web Sweeps ---

def run_tier1():
    run_id = agent_start("ambient-research", "Tier 1 sweep")
    log("=== Tier 1 Run Starting ===")
    try:
        _run_tier1_inner(run_id)
    except Exception as e:
        agent_fail("ambient-research", run_id, str(e))
        raise


def _run_tier1_inner(run_id):
    if not check_machine("soy-1"):
        log("Razer offline — skipping Tier 1")
        return

    db = get_db()
    streams = db.execute("SELECT * FROM research_streams WHERE active = 1 ORDER BY priority DESC").fetchall()
    finding_count = 0

    for stream in streams:
        # Check cadence
        last = db.execute(
            "SELECT completed_at FROM research_tasks WHERE stream_id=? AND tier=1 AND status='completed' ORDER BY completed_at DESC LIMIT 1",
            (stream["id"],),
        ).fetchone()
        cadence = stream["tier_1_cadence_hours"] or 6
        if last and last["completed_at"]:
            elapsed = (datetime.now() - datetime.fromisoformat(last["completed_at"])).total_seconds() / 3600
            if elapsed < cadence:
                log(f"  {stream['name']}: skipping (last run {elapsed:.1f}h ago, cadence {cadence}h)")
                continue

        keywords = json.loads(stream["keywords"]) if stream["keywords"] else [stream["name"]]
        prompt = (
            f"Research the current state of: {stream['name']}\n\n"
            f"Focus areas and keywords: {', '.join(keywords)}\n\n"
            f"Context: {stream['description']}\n\n"
            "Provide a structured analysis of recent developments, key tools/techniques, "
            "emerging trends, and anything that represents a meaningful shift. "
            "Be specific — name tools, cite approaches, describe techniques."
        )

        # Create task
        db.execute(
            "INSERT INTO research_tasks (stream_id, tier, task_type, prompt, model, machine, status, started_at) VALUES (?, 1, 'web_sweep', ?, 'mistral:7b', 'soy-1', 'running', datetime('now'))",
            (stream["id"], prompt),
        )
        db.commit()
        task_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

        log(f"  {stream['name']}: Tier 1 sweep (task {task_id})...")
        m = MACHINES["soy-1"]
        result = ollama_generate(m["ip"], m["port"], "mistral:7b", prompt, SYSTEM_PROMPTS["web_sweep"])

        if "error" in result:
            log(f"    FAILED: {result['error'][:100]}")
            db.execute("UPDATE research_tasks SET status='failed', error=?, completed_at=datetime('now') WHERE id=?", (result["error"], task_id))
        else:
            log(f"    OK: {result['tokens_out']} tokens | {result['eval_rate']} tok/s | {result['duration_ms']}ms")
            db.execute(
                "UPDATE research_tasks SET status='completed', output_data=?, tokens_out=?, duration_ms=?, completed_at=datetime('now') WHERE id=?",
                (json.dumps({"response": result["response"], "eval_rate": result["eval_rate"]}), result["tokens_out"], result["duration_ms"], task_id),
            )
            db.execute(
                "INSERT INTO research_findings (stream_id, task_id, tier, finding_type, title, content) VALUES (?, ?, 1, 'insight', ?, ?)",
                (stream["id"], task_id, f"Tier 1 Sweep — {stream['name']} — {datetime.now().strftime('%Y-%m-%d')}", result["response"]),
            )
            finding_count += 1
        db.commit()

    db.close()
    agent_complete("ambient-research", run_id, f"Tier 1: {finding_count} findings across {len(streams)} streams")
    log("=== Tier 1 Run Complete ===")


# --- Tier 2: Summarize + Wiki Update ---

def run_tier2():
    run_id = agent_start("ambient-research", "Tier 2 wiki update")
    log("=== Tier 2 Run Starting ===")
    try:
        _run_tier2_inner(run_id)
    except Exception as e:
        agent_fail("ambient-research", run_id, str(e))
        raise


def _run_tier2_inner(run_id):
    # Prefer Legion (RTX 5080, gemma4:e2b @ 164 tok/s), fall back to Lucy
    # (RTX 3080 Ti, qwen2.5:14b @ 27 tok/s). Legion is always-on but its GPU
    # may be handed off to gaming via Sunshine — check_machine catches that.
    t2_machine = None
    t2_model = None
    if check_machine("legion"):
        t2_machine, t2_model = "legion", "gemma4:e2b"
    elif check_machine("lucy"):
        t2_machine, t2_model = "lucy", "qwen2.5:14b"
    else:
        log("No Tier 2 machines available — skipping")
        return

    db = get_db()
    streams = db.execute("SELECT * FROM research_streams WHERE active = 1 ORDER BY priority DESC").fetchall()
    wiki_updates = 0

    for stream in streams:
        # Check for unincorporated findings
        findings = db.execute(
            "SELECT content FROM research_findings WHERE stream_id=? AND incorporated=0 ORDER BY created_at DESC LIMIT 10",
            (stream["id"],),
        ).fetchall()
        if not findings:
            log(f"  {stream['name']}: no new findings — skipping")
            continue

        # Check cadence
        last = db.execute(
            "SELECT completed_at FROM research_tasks WHERE stream_id=? AND tier=2 AND status='completed' ORDER BY completed_at DESC LIMIT 1",
            (stream["id"],),
        ).fetchone()
        cadence = stream["tier_2_cadence_hours"] or 12
        if last and last["completed_at"]:
            elapsed = (datetime.now() - datetime.fromisoformat(last["completed_at"])).total_seconds() / 3600
            if elapsed < cadence:
                log(f"  {stream['name']}: skipping (last run {elapsed:.1f}h ago, cadence {cadence}h)")
                continue

        # Get current wiki
        wiki = db.execute("SELECT id, content FROM research_wikis WHERE stream_id=? ORDER BY updated_at DESC LIMIT 1", (stream["id"],)).fetchone()
        wiki_text = wiki["content"] if wiki else "(No wiki yet — create the initial version.)"
        findings_text = "\n\n---\n\n".join(f["content"] for f in findings)

        prompt = (
            f"Stream: {stream['name']}\n\n"
            f"## Current Wiki Document\n\n{wiki_text}\n\n"
            f"## New Findings to Incorporate\n\n{findings_text}\n\n"
            "Produce an updated wiki document. Restructure as needed — this should read as a coherent, current reference."
        )

        db.execute(
            "INSERT INTO research_tasks (stream_id, tier, task_type, prompt, model, machine, status, started_at) VALUES (?, 2, 'wiki_update', ?, ?, ?, 'running', datetime('now'))",
            (stream["id"], prompt, t2_model, t2_machine),
        )
        db.commit()
        task_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

        log(f"  {stream['name']}: Tier 2 wiki update (task {task_id}, {t2_machine}/{t2_model})...")
        m = MACHINES[t2_machine]
        result = ollama_generate(m["ip"], m["port"], t2_model, prompt, SYSTEM_PROMPTS["wiki_update"], timeout=600)

        if "error" in result:
            log(f"    FAILED: {result['error'][:100]}")
            db.execute("UPDATE research_tasks SET status='failed', error=?, completed_at=datetime('now') WHERE id=?", (result["error"], task_id))
        else:
            log(f"    OK: {result['tokens_out']} tokens | {result['eval_rate']} tok/s | {result['duration_ms']}ms")
            db.execute(
                "UPDATE research_tasks SET status='completed', output_data=?, tokens_out=?, duration_ms=?, completed_at=datetime('now') WHERE id=?",
                (json.dumps({"response": result["response"], "eval_rate": result["eval_rate"]}), result["tokens_out"], result["duration_ms"], task_id),
            )

            # Update or create wiki
            if wiki:
                db.execute("UPDATE research_wikis SET content=?, version=version+1, word_count=?, last_synthesized_at=datetime('now'), updated_at=datetime('now') WHERE id=?",
                    (result["response"], len(result["response"].split()), wiki["id"]))
                version = db.execute("SELECT version FROM research_wikis WHERE id=?", (wiki["id"],)).fetchone()[0]
                db.execute("INSERT INTO research_wiki_versions (wiki_id, version, content, change_summary) VALUES (?, ?, ?, ?)",
                    (wiki["id"], version, result["response"], f"Integrated {len(findings)} Tier 1 findings"))
            else:
                db.execute("INSERT INTO research_wikis (stream_id, title, content, version, word_count, last_synthesized_at) VALUES (?, ?, ?, 1, ?, datetime('now'))",
                    (stream["id"], f"{stream['name']} — Research Wiki", result["response"], len(result["response"].split())))

            db.execute("UPDATE research_findings SET incorporated=1 WHERE stream_id=? AND incorporated=0", (stream["id"],))
            wiki_updates += 1

        db.commit()

    db.close()
    agent_complete("ambient-research", run_id, f"Tier 2: {wiki_updates} wiki updates across {len(streams)} streams")
    log("=== Tier 2 Run Complete ===")


# --- Tier 3: Claude CLI Overnight ---

def run_tier3():
    run_id = agent_start("ambient-research", "Tier 3 Claude CLI digest")
    log("=== Tier 3 Run Starting (Claude CLI) ===")
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("digest", Path(__file__).parent / "digest.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        content = mod.generate_digest()
        if content:
            log(f"  Digest generated: {len(content)} chars")
            agent_complete("ambient-research", run_id, f"Tier 3: digest generated ({len(content)} chars)")
        else:
            log("  Digest generation returned empty")
            agent_complete("ambient-research", run_id, "Tier 3: digest empty")
        log("=== Tier 3 Run Complete ===")
    except Exception as e:
        agent_fail("ambient-research", run_id, str(e))
        raise


# --- Status ---

def show_status():
    db = get_db()
    streams = db.execute("SELECT * FROM research_streams WHERE active = 1").fetchall()
    print(f"\n{'='*60}")
    print(f"  AMBIENT RESEARCH — Pipeline Status")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}\n")

    for s in streams:
        print(f"  [{s['name']}] priority={s['priority']}")
        for tier in (1, 2, 3):
            last = db.execute(
                "SELECT status, completed_at FROM research_tasks WHERE stream_id=? AND tier=? ORDER BY completed_at DESC LIMIT 1",
                (s["id"], tier),
            ).fetchone()
            if last:
                print(f"    Tier {tier}: {last['status']} @ {last['completed_at'] or '—'}")
            else:
                print(f"    Tier {tier}: no runs")

        fc = db.execute("SELECT COUNT(*) as n FROM research_findings WHERE stream_id=?", (s["id"],)).fetchone()
        uc = db.execute("SELECT COUNT(*) as n FROM research_findings WHERE stream_id=? AND incorporated=0", (s["id"],)).fetchone()
        wiki = db.execute("SELECT version, word_count, updated_at FROM research_wikis WHERE stream_id=? ORDER BY updated_at DESC LIMIT 1", (s["id"],)).fetchone()
        print(f"    Findings: {fc['n']} total, {uc['n']} pending")
        if wiki:
            print(f"    Wiki: v{wiki['version']}, {wiki['word_count']} words, updated {wiki['updated_at']}")
        print()

    print("  === Machines ===")
    for name, m in MACHINES.items():
        ok = check_machine(name)
        print(f"    {name}: {'ONLINE' if ok else 'OFFLINE'} ({m['ip']})")
    print()
    db.close()


def show_network():
    print("\n  === Ollama Network ===\n")
    for name, m in MACHINES.items():
        ok = check_machine(name)
        if ok:
            try:
                req = urllib.request.Request(f"http://{m['ip']}:{m['port']}/api/tags")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read())
                    models = [md["name"] for md in data.get("models", [])]
                print(f"  {name}: ONLINE — {', '.join(models)}")
            except Exception:
                print(f"  {name}: ONLINE (error reading models)")
        else:
            print(f"  {name}: OFFLINE")
    print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 run.py [tier1|tier2|tier3|status|network]")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "tier1":
        run_tier1()
    elif cmd == "tier2":
        run_tier2()
    elif cmd == "tier3":
        run_tier3()
    elif cmd == "status":
        show_status()
    elif cmd == "network":
        show_network()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
