#!/usr/bin/env python3
"""
Model Benchmarker — Detects new Ollama models and benchmarks them against
current workloads. Reports results to Discord.

Runs daily. Diffs current model list against stored snapshot. When new models
appear, runs a standard benchmark suite and posts results.

Usage:
    python3 shared/model_benchmark.py              # Check for new models + benchmark
    python3 shared/model_benchmark.py benchmark     # Force benchmark all models
    python3 shared/model_benchmark.py status        # Show stored benchmark results
"""

import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(PLUGIN_ROOT, "data", "soy.db")
DATA_DIR = Path.home() / ".local" / "share" / "software-of-you"
LOG_FILE = DATA_DIR / "model-benchmark.log"
ENV_PATH = os.path.join(PLUGIN_ROOT, ".env")

MACHINES = {
    "soy-1": {"ip": "100.91.234.67", "port": 11434},
    "lucy": {"ip": "100.74.238.16", "port": 11434},
    "legion": {"ip": "legion", "port": 11434},
}

# Standard benchmark prompts — representative of actual SoY workloads
BENCHMARK_PROMPTS = {
    "research_sweep": {
        "system": "You are a research assistant. Analyze the topic and provide a structured summary of recent developments, key tools, and emerging trends.",
        "prompt": "Research the current state of local LLM deployment for personal AI assistants. Focus on quantization techniques, context window management, and multi-model routing strategies.",
        "description": "Tier 1 research sweep (ambient research)",
    },
    "json_extraction": {
        "system": "You are a data extractor. Respond with ONLY a valid JSON object, no explanation.",
        "prompt": 'Extract structured data from this text: "Sarah Chen, VP of Engineering at Acme Corp, mentioned in our call that they\'re moving to microservices by Q3. She wants a proposal by next Friday. Budget is around $50K." Return: {"name": "", "role": "", "company": "", "key_info": "", "deadline": "", "budget": ""}',
        "description": "JSON extraction (triage, evaluator)",
    },
    "wiki_synthesis": {
        "system": "You are a wiki editor. Given findings, produce a coherent summary document.",
        "prompt": "Synthesize these findings into a wiki section:\n1. Ollama 0.6 adds native tool calling support\n2. GGUF Q4_K_M quantization shows only 2% quality loss vs FP16\n3. vLLM now supports speculative decoding for 2x throughput\n4. Context window packing can fit 3x more examples in the same token budget",
        "description": "Tier 2 wiki synthesis (ambient research)",
    },
}

# Discord integration
def load_env():
    env = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip("'\"")
    return env


ENV = load_env()
BOT_TOKEN = ENV.get("DISCORD_BOT_TOKEN", "")
GUILD_ID = ENV.get("DISCORD_GUILD_ID", "868529770114215936")
DISCORD_API = "https://discord.com/api/v10"


def discord_request(method, endpoint, data=None):
    cmd = ["curl", "-s", "-X", method,
           "-H", f"Authorization: Bot {BOT_TOKEN}",
           "-H", "Content-Type: application/json",
           f"{DISCORD_API}{endpoint}"]
    if data:
        cmd.extend(["-d", json.dumps(data)])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return json.loads(result.stdout) if result.stdout.strip() else None
    except Exception:
        return None


def find_or_create_channel():
    """Find #soy-benchmarks channel or create it."""
    channels = discord_request("GET", f"/guilds/{GUILD_ID}/channels")
    if channels:
        for ch in channels:
            if ch.get("name") == "soy-benchmarks":
                return ch["id"]

    # Create it
    result = discord_request("POST", f"/guilds/{GUILD_ID}/channels", {
        "name": "soy-benchmarks",
        "type": 0,
        "topic": "Automated model benchmark results from Son of Anton",
    })
    return result["id"] if result else None


def send_discord_embed(channel_id, title, description, color=0x5865F2, fields=None):
    embed = {"title": title, "description": description[:4096], "color": color}
    if fields:
        embed["fields"] = fields[:25]
    embed["footer"] = {"text": f"Son of Anton Benchmarker • {datetime.now().strftime('%Y-%m-%d %H:%M')}"}
    discord_request("POST", f"/channels/{channel_id}/messages", {"embeds": [embed]})


def log(msg):
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


def ollama_list(ip, port):
    """Get list of models from an Ollama instance."""
    try:
        req = urllib.request.Request(f"http://{ip}:{port}/api/tags")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return {m["name"]: m.get("size", 0) for m in data.get("models", [])}
    except Exception:
        return {}


def ollama_generate(ip, port, model, prompt, system, timeout=120):
    """Run a generation and return timing metrics."""
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
        wall_time = time.time() - start
        tokens_out = data.get("eval_count", 0)
        eval_duration = data.get("eval_duration", 1) / 1e9  # nanoseconds to seconds
        tok_s = round(tokens_out / eval_duration, 1) if eval_duration > 0 else 0
        response = data.get("response", "")
        return {
            "tokens_out": tokens_out,
            "tok_s": tok_s,
            "wall_time_s": round(wall_time, 1),
            "response_len": len(response),
            "response_preview": response[:200],
        }
    except Exception as e:
        return {"error": str(e)}


def get_stored_models(db):
    """Get the last known model list from soy_meta."""
    row = db.execute("SELECT value FROM soy_meta WHERE key = 'ollama_model_snapshot'").fetchone()
    if row:
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            pass
    return {}


def store_models(db, models_by_machine):
    """Save current model list to soy_meta."""
    db.execute(
        "INSERT OR REPLACE INTO soy_meta (key, value, updated_at) VALUES ('ollama_model_snapshot', ?, datetime('now'))",
        (json.dumps(models_by_machine),),
    )
    db.commit()


def store_benchmark(db, machine, model, task, results):
    """Store benchmark results."""
    db.execute("""
        INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
        VALUES ('benchmark', 0, 'model_benchmark', ?, datetime('now'))
    """, (json.dumps({
        "machine": machine, "model": model, "task": task,
        **results,
    }),))
    db.commit()


def run_benchmark(machine_name, model, force=False):
    """Run the full benchmark suite for a model on a machine."""
    m = MACHINES[machine_name]
    results = {}

    for task_name, task in BENCHMARK_PROMPTS.items():
        log(f"    {task_name}...")
        result = ollama_generate(m["ip"], m["port"], model, task["prompt"], task["system"])
        results[task_name] = result
        if "error" not in result:
            log(f"      {result['tokens_out']} tokens, {result['tok_s']} tok/s, {result['wall_time_s']}s")
        else:
            log(f"      ERROR: {result['error'][:80]}")

    return results


def check_and_benchmark():
    """Main entry: check for new models, benchmark if found."""
    db = get_db()
    stored = get_stored_models(db)
    current = {}
    new_models = []

    log("=== Model Benchmark Check ===")

    for machine_name, m in MACHINES.items():
        models = ollama_list(m["ip"], m["port"])
        if models:
            current[machine_name] = list(models.keys())
            old_models = set(stored.get(machine_name, []))
            new_in_machine = set(models.keys()) - old_models
            if new_in_machine:
                for model in new_in_machine:
                    new_models.append((machine_name, model))
                log(f"  {machine_name}: {len(new_in_machine)} new model(s) — {', '.join(new_in_machine)}")
            else:
                log(f"  {machine_name}: {len(models)} models, no changes")
        else:
            log(f"  {machine_name}: offline")

    if not new_models:
        log("No new models detected")
        store_models(db, current)
        db.close()
        return

    # Benchmark new models
    log(f"\nBenchmarking {len(new_models)} new model(s)...")
    all_results = []

    for machine_name, model in new_models:
        log(f"  {machine_name}/{model}:")
        results = run_benchmark(machine_name, model)
        store_benchmark(db, machine_name, model, "full_suite", results)
        all_results.append((machine_name, model, results))

    # Update stored snapshot
    store_models(db, current)
    db.close()

    # Post to Discord
    post_results_to_discord(all_results)

    log("=== Benchmark Complete ===")


def force_benchmark():
    """Benchmark all currently loaded models."""
    db = get_db()
    log("=== Force Benchmark — All Models ===")
    all_results = []

    for machine_name, m in MACHINES.items():
        models = ollama_list(m["ip"], m["port"])
        if not models:
            log(f"  {machine_name}: offline")
            continue

        for model in models:
            log(f"  {machine_name}/{model}:")
            results = run_benchmark(machine_name, model)
            store_benchmark(db, machine_name, model, "full_suite", results)
            all_results.append((machine_name, model, results))

    db.close()

    if all_results:
        post_results_to_discord(all_results)

    log("=== Force Benchmark Complete ===")


def post_results_to_discord(all_results):
    """Post benchmark results to Discord."""
    if not BOT_TOKEN:
        log("No Discord bot token — skipping notification")
        return

    channel_id = find_or_create_channel()
    if not channel_id:
        log("Could not find/create #soy-benchmarks channel")
        return

    for machine_name, model, results in all_results:
        fields = []
        avg_tok_s = []

        for task_name, task_info in BENCHMARK_PROMPTS.items():
            r = results.get(task_name, {})
            if "error" in r:
                value = f"ERROR: {r['error'][:60]}"
            else:
                tok_s = r.get("tok_s", 0)
                avg_tok_s.append(tok_s)
                value = f"{tok_s} tok/s • {r.get('tokens_out', 0)} tokens • {r.get('wall_time_s', 0)}s"

            fields.append({
                "name": task_info["description"],
                "value": value,
                "inline": False,
            })

        avg = sum(avg_tok_s) / len(avg_tok_s) if avg_tok_s else 0
        color = 0x57F287 if avg > 50 else 0xFEE75C if avg > 20 else 0xED4245

        send_discord_embed(
            channel_id,
            f"🧪 {model} on {machine_name}",
            f"Average: **{avg:.1f} tok/s** across {len(avg_tok_s)} tasks",
            color=color,
            fields=fields,
        )
        log(f"  Posted to Discord: {model} on {machine_name} ({avg:.1f} avg tok/s)")


def show_status():
    """Show stored benchmark data."""
    db = get_db()

    print(f"\n{'='*60}")
    print(f"  MODEL BENCHMARKS — Stored Results")
    print(f"{'='*60}\n")

    stored = get_stored_models(db)
    for machine, models in stored.items():
        print(f"  {machine}: {', '.join(models)}")
    print()

    recent = db.execute("""
        SELECT details, created_at FROM activity_log
        WHERE entity_type = 'benchmark' AND action = 'model_benchmark'
        ORDER BY created_at DESC LIMIT 10
    """).fetchall()

    if recent:
        print("  Recent Benchmarks:")
        for r in recent:
            d = json.loads(r["details"])
            print(f"    [{r['created_at']}] {d.get('machine')}/{d.get('model')}")
            for task in BENCHMARK_PROMPTS:
                t = d.get(task, {})
                if "error" not in t:
                    print(f"      {task}: {t.get('tok_s', '?')} tok/s")
    else:
        print("  No benchmark data yet")

    print()
    db.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "check":
        check_and_benchmark()
    elif cmd == "benchmark":
        force_benchmark()
    elif cmd == "status":
        show_status()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
