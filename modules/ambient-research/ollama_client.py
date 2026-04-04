"""
Ollama client for the ambient research pipeline.
Talks to Ollama instances on the Tailscale mesh network.
"""

import json
import urllib.request
import urllib.error
import time
import sqlite3
from pathlib import Path

DB_PATH = Path.home() / ".local" / "share" / "software-of-you" / "soy.db"

# Machine-to-tier pinning: each machine runs ONE primary role to avoid VRAM thrashing.
# Razer (6GB) = T1 only (Mistral 7B, always loaded, instant)
# Lucy (12GB) = T2 primary (Qwen 14B, always loaded, no swap penalty)
# Legion (16GB) = burst capacity (Gemma e4b for function calling, large models when available)
MACHINES = {
    "razer": {
        "ip": "100.125.139.126",
        "port": 11434,
        "tier": 1,
        "models": ["mistral:7b", "llama3.1:8b"],
    },
    "lucy": {
        "ip": "100.74.238.16",
        "port": 11434,
        "tier": 2,
        "models": ["qwen2.5:14b", "mistral:7b"],
    },
    "legion": {
        "ip": "100.69.255.78",
        "port": 11434,
        "tier": 2,
        "models": ["qwen2.5:32b", "deepseek-r1:32b", "qwen3:30b-a3b", "gemma4:e4b", "mistral:7b"],
    },
}


def _url(machine: str, endpoint: str) -> str:
    m = MACHINES[machine]
    return f"http://{m['ip']}:{m['port']}{endpoint}"


def check_health(machine: str, timeout: float = 5.0) -> bool:
    """Check if an Ollama instance is reachable."""
    try:
        req = urllib.request.Request(_url(machine, "/api/tags"))
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def list_models(machine: str, timeout: float = 5.0) -> list[str]:
    """List available models on a machine."""
    try:
        req = urllib.request.Request(_url(machine, "/api/tags"))
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            return [m["name"] for m in data.get("models", [])]
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return []


def generate(
    machine: str,
    model: str,
    prompt: str,
    system: str = None,
    temperature: float = 0.3,
    timeout: float = 120.0,
) -> dict:
    """
    Generate a completion from an Ollama model.

    Returns dict with keys: response, tokens_in, tokens_out, duration_ms, model, machine
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
        _url(machine, "/api/generate"),
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
            "machine": machine,
            "model": model,
        }
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return {
            "error": f"Connection failed: {str(e)}",
            "machine": machine,
            "model": model,
        }

    return {
        "response": data.get("response", ""),
        "tokens_in": data.get("prompt_eval_count", 0),
        "tokens_out": data.get("eval_count", 0),
        "duration_ms": int((time.time() - start) * 1000),
        "eval_rate": (
            round(data.get("eval_count", 0) / (data.get("eval_duration", 1) / 1e9), 1)
            if data.get("eval_duration")
            else None
        ),
        "model": model,
        "machine": machine,
    }


def _is_gpu_available(machine: str) -> bool:
    """Check if a machine is flagged active in research_machines (GPU not in use by games).

    Returns True if the machine has no DB entry (default to available) or active=1.
    Returns False only if explicitly flagged active=0 (GPU handed off to gaming).
    """
    try:
        db = sqlite3.connect(DB_PATH)
        row = db.execute(
            "SELECT active FROM research_machines WHERE name = ?", (machine,)
        ).fetchone()
        db.close()
        if row is None:
            return True  # No DB entry = assume available
        return bool(row[0])
    except Exception:
        return True  # DB error = don't block routing


def pick_machine(tier: int) -> str | None:
    """Pick the best available machine for a given tier.

    Checks the research_machines.active flag first (instant) to skip machines
    whose GPU is handed off to gaming, avoiding the 5s health-check timeout.

    For T2: prefers Lucy (always-on, 12GB) over Legion (intermittent, gaming).
    """
    candidates = [name for name, m in MACHINES.items() if m["tier"] == tier]
    if not candidates:
        # Fall back: any machine
        candidates = list(MACHINES.keys())

    # Prefer always-on machines first (Lucy before Legion for T2)
    prefer_order = ["lucy", "razer", "legion"]
    candidates.sort(key=lambda n: prefer_order.index(n) if n in prefer_order else 99)

    for name in candidates:
        if _is_gpu_available(name) and check_health(name):
            return name
    return None


def pick_model(machine: str, tier: int) -> str | None:
    """Pick the best model on a machine for the given tier."""
    available = list_models(machine)
    if not available:
        return None

    # Tier 2 prefers largest available (32b > 30b > 14b)
    if tier == 2:
        for size in ["32b", "30b", "14b"]:
            for m in available:
                if size in m:
                    return m

    # Tier 1 prefers 7B Mistral or Llama
    if tier == 1:
        for m in available:
            if "mistral" in m and "7b" in m:
                return m
        for m in available:
            if "7b" in m or "8b" in m:
                return m

    return available[0] if available else None


def register_machines_in_db():
    """Register/update machine entries in the database."""
    db = sqlite3.connect(DB_PATH)
    for name, info in MACHINES.items():
        healthy = check_health(name, timeout=3)
        models = list_models(name, timeout=3) if healthy else []
        db.execute(
            """INSERT INTO research_machines (name, tailscale_ip, ollama_port, models, tier, active, last_seen_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, CASE WHEN ? THEN datetime('now') ELSE NULL END, datetime('now'))
               ON CONFLICT(name) DO UPDATE SET
                   models = excluded.models,
                   active = excluded.active,
                   last_seen_at = CASE WHEN excluded.active THEN datetime('now') ELSE last_seen_at END,
                   updated_at = datetime('now')""",
            (name, info["ip"], info["port"], json.dumps(models), info["tier"], int(healthy), healthy),
        )
    db.commit()
    db.close()


if __name__ == "__main__":
    # Quick diagnostic
    print("=== Ambient Research — Ollama Network Status ===\n")
    for name in MACHINES:
        healthy = check_health(name)
        status = "ONLINE" if healthy else "OFFLINE"
        models = list_models(name) if healthy else []
        print(f"  {name}: {status}")
        if models:
            for m in models:
                print(f"    - {m}")
    print()

    # Quick generation test
    for name in MACHINES:
        if check_health(name):
            model = pick_model(name, MACHINES[name]["tier"])
            if model:
                print(f"Testing {name}/{model}...")
                result = generate(name, model, "Say 'ready' in one word.")
                if "error" in result:
                    print(f"  ERROR: {result['error']}")
                else:
                    print(f"  Response: {result['response'].strip()}")
                    print(f"  Speed: {result['eval_rate']} tok/s | Duration: {result['duration_ms']}ms")
                print()
