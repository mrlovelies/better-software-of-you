#!/usr/bin/env python3
"""
Paperclip Bridge — Connects the Signal Harvester pipeline to Paperclip's
agent dispatch system.

Instead of calling Ollama/Claude directly, the pipeline creates issues
in Paperclip and lets it dispatch to the appropriate agent.

Usage:
  python3 paperclip_bridge.py status                    # check Paperclip health + agent status
  python3 paperclip_bridge.py dispatch-triage [--limit=10]  # create triage issues for pending signals
  python3 paperclip_bridge.py dispatch-build <signal_id>    # dispatch a build for an approved signal
  python3 paperclip_bridge.py dispatch-competitive [--limit=10] # dispatch competitive analysis
  python3 paperclip_bridge.py runs                      # list recent heartbeat runs
"""

import sys
import os
import json
import sqlite3
import argparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError

PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(PLUGIN_ROOT, "data", "soy.db")

# Paperclip API — running on Razer (bound to 127.0.0.1:3100)
# When running from another machine, use SSH tunnel: ssh -L 3100:127.0.0.1:3100 mrlovelies@100.91.234.67
# Or set PAPERCLIP_API=http://localhost:3100/api after establishing the tunnel
PAPERCLIP_API = os.environ.get("PAPERCLIP_API", "http://localhost:3100/api")

# Agent and company IDs (from Paperclip registration)
COMPANY_ID = "c7076754-2733-400d-91c3-02c8ee85646b"
TRIAGE_AGENT_ID = "82e03b0c-c6b6-40e0-9ab9-086cf3c7c77b"
BUILDER_AGENT_ID = "32fd42a7-0cea-4ecd-8b75-17ae19cd4269"
COMPETITIVE_AGENT_ID = "6e181ea5-3177-4573-8258-f58c63cd58ec"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def paperclip_request(method, endpoint, data=None):
    """Call Paperclip API."""
    url = f"{PAPERCLIP_API}{endpoint}"
    body = json.dumps(data).encode() if data else None
    req = Request(url, data=body, headers={"Content-Type": "application/json"}, method=method)
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        error_body = e.read().decode() if e.fp else ""
        print(f"  [error] Paperclip {method} {endpoint}: {e.code} {error_body[:200]}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  [error] Paperclip {method} {endpoint}: {e}", file=sys.stderr)
        return None


def cmd_status(args):
    """Check Paperclip health and agent status."""
    health = paperclip_request("GET", "/health")
    if not health:
        print("Paperclip is not reachable.")
        return

    print(f"Paperclip: {health.get('status')} (v{health.get('version')})")
    print(f"  Mode: {health.get('deploymentMode')}")
    print(f"  Auth: {health.get('authReady')}")

    agents = paperclip_request("GET", f"/companies/{COMPANY_ID}/agents")
    if agents:
        print(f"\nAgents ({len(agents)}):")
        for a in agents:
            last_hb = a.get("lastHeartbeatAt", "never")
            print(f"  {a['name']} ({a['role']}) — {a['status']} — last heartbeat: {last_hb}")


def cmd_dispatch_triage(args):
    """Create triage issues for pending signals."""
    db = get_db()

    # Get signals that passed T1 but haven't been scored yet
    signals = db.execute("""
        SELECT s.id, s.raw_text, s.subreddit, s.upvotes, s.signal_type
        FROM harvest_signals s
        JOIN harvest_triage t ON t.signal_id = s.id
        WHERE t.verdict = 'pending' AND t.composite_score IS NULL
        ORDER BY s.upvotes DESC
        LIMIT ?
    """, (args.limit,)).fetchall()

    if not signals:
        print("No signals pending triage.")
        return

    # Create a batch issue
    signal_summaries = []
    for s in signals:
        summary = s["raw_text"][:200].replace("\n", " ")
        signal_summaries.append(f"- Signal #{s['id']} (r/{s['subreddit']}, {s['upvotes']}↑): {summary}")

    description = f"""Evaluate these {len(signals)} signals for market viability.

For each signal, use the local Ollama models:
1. Run T2 scoring with qwen2.5:7b at http://localhost:11434
2. Score: market_size (1-10), monetization (1-10), build_complexity (1-10, 10=easy), existing_solutions (1-10, 10=unserved), soy_leaf_fit (1-10)
3. Extract: pain point summary, industry, target audience, existing solutions, monetization model
4. Store results in harvest_triage table in the SoY database at {DB_PATH}

Signals to evaluate:
{chr(10).join(signal_summaries)}

Use python3 shared/signal_triage.py score --limit {len(signals)} --model qwen2.5:7b
"""

    issue = paperclip_request("POST", f"/companies/{COMPANY_ID}/issues", {
        "title": f"Triage batch: {len(signals)} signals pending scoring",
        "description": description,
        "assigneeAgentId": TRIAGE_AGENT_ID,
        "priority": "medium",
    })

    if issue:
        print(f"Created issue {issue.get('identifier')}: {issue.get('title')}")
        print(f"  Assigned to: Signal Triage Agent")
        sig_ids = ', '.join([f'#{s["id"]}' for s in signals])
        print(f"  Signals: {sig_ids}")

        # Log in activity
        db.execute("""
            INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
            VALUES ('paperclip', 0, 'triage_dispatched', ?, datetime('now'))
        """, (json.dumps({
            "issue_id": issue.get("id"),
            "identifier": issue.get("identifier"),
            "signal_count": len(signals),
        }),))
        db.commit()
    else:
        print("Failed to create issue.")

    db.close()


def cmd_dispatch_build(args):
    """Dispatch a build for an approved signal or forecast."""
    db = get_db()

    signal = db.execute("""
        SELECT s.*, t.extracted_pain, t.target_audience, t.monetization_model,
               t.existing_solutions, t.build_estimate, t.composite_score
        FROM harvest_signals s
        JOIN harvest_triage t ON t.signal_id = s.id
        WHERE s.id = ? AND t.verdict = 'approved'
    """, (args.signal_id,)).fetchone()

    if not signal:
        print(f"Signal #{args.signal_id} not found or not approved.")
        return

    description = f"""Build a product/solution for this approved signal.

SIGNAL:
  Pain: {signal['extracted_pain'] or signal['raw_text'][:300]}
  Industry: {signal['industry'] or 'Unknown'}
  Audience: {signal['target_audience'] or 'Unknown'}
  Monetization: {signal['monetization_model'] or 'TBD'}
  Existing solutions: {signal['existing_solutions'] or 'None known'}
  Composite score: {signal['composite_score']}/10
  Source: {signal['source_url']}

INSTRUCTIONS:
1. Research the problem space and validate the opportunity
2. Design a minimal viable solution (SoY leaf if leaf_fit >= 7, otherwise standalone)
3. Scaffold the project in the leaves/ directory
4. Build the core functionality
5. Create a manifest.json following the leaf spec at docs/leaf-package-spec.md
6. Update harvest_builds table with status and output path

{f'CUSTOM INSTRUCTIONS: {args.instructions}' if args.instructions else ''}
"""

    issue = paperclip_request("POST", f"/companies/{COMPANY_ID}/issues", {
        "title": f"Build: {signal['extracted_pain'][:80] if signal['extracted_pain'] else 'Solution for signal #' + str(args.signal_id)}",
        "description": description,
        "assigneeAgentId": BUILDER_AGENT_ID,
        "priority": "high",
    })

    if issue:
        print(f"Created build issue {issue.get('identifier')}: {issue.get('title')}")
        print(f"  Assigned to: Builder Agent")

        # Create build entry
        db.execute("""
            INSERT INTO harvest_builds
                (triage_id, project_name, build_type, status, agent_framework, spec)
            VALUES (
                (SELECT id FROM harvest_triage WHERE signal_id = ?),
                ?, 'standalone_saas', 'queued', 'paperclip',
                ?
            )
        """, (
            args.signal_id,
            f"Build from signal #{args.signal_id}",
            json.dumps({"paperclip_issue_id": issue.get("id"), "signal_id": args.signal_id}),
        ))
        db.commit()
    else:
        print("Failed to create build issue.")

    db.close()


def cmd_dispatch_competitive(args):
    """Dispatch competitive analysis for unanalyzed signals."""
    db = get_db()

    signals = db.execute("""
        SELECT id, raw_text, subreddit, upvotes
        FROM competitive_signals
        WHERE complaint_summary IS NULL
        ORDER BY upvotes DESC
        LIMIT ?
    """, (args.limit,)).fetchall()

    if not signals:
        print("No competitive signals pending analysis.")
        return

    signal_summaries = []
    for s in signals:
        summary = s["raw_text"][:150].replace("\n", " ")
        signal_summaries.append(f"- Signal #{s['id']} (r/{s['subreddit']}, {s['upvotes']}↑): {summary}")

    description = f"""Analyze these {len(signals)} competitive intelligence signals.

For each signal:
1. Determine if it's a genuine product complaint (T1 filter with mistral:7b at http://localhost:11434)
2. If yes, extract: target product, company, category, complaint type, missing features, sentiment
3. Score: market_size, switchability, build_advantage, revenue_opportunity (all 1-10)
4. Store results in competitive_signals and competitive_targets tables

Use python3 shared/competitive_intel.py analyze --limit {len(signals)}

Signals:
{chr(10).join(signal_summaries)}
"""

    issue = paperclip_request("POST", f"/companies/{COMPANY_ID}/issues", {
        "title": f"Competitive analysis: {len(signals)} signals",
        "description": description,
        "assigneeAgentId": COMPETITIVE_AGENT_ID,
        "priority": "medium",
    })

    if issue:
        print(f"Created issue {issue.get('identifier')}: {issue.get('title')}")
    else:
        print("Failed to create issue.")

    db.close()


def cmd_runs(args):
    """List recent heartbeat runs."""
    runs = paperclip_request("GET", f"/companies/{COMPANY_ID}/heartbeat-runs?limit=10")
    if not runs:
        print("No runs found (or API not accessible).")
        return

    if isinstance(runs, list):
        for r in runs:
            print(f"  {r.get('id', '?')[:8]} | {r.get('status', '?')} | agent: {r.get('agentId', '?')[:8]} | {r.get('createdAt', '?')}")
    else:
        print(f"Unexpected response: {json.dumps(runs)[:200]}")


def main():
    parser = argparse.ArgumentParser(description="Paperclip Bridge — pipeline dispatch")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("status", help="Check Paperclip health")

    p_triage = subparsers.add_parser("dispatch-triage", help="Dispatch triage batch")
    p_triage.add_argument("--limit", type=int, default=10)

    p_build = subparsers.add_parser("dispatch-build", help="Dispatch a build")
    p_build.add_argument("signal_id", type=int)
    p_build.add_argument("--instructions", help="Custom build instructions")

    p_comp = subparsers.add_parser("dispatch-competitive", help="Dispatch competitive analysis")
    p_comp.add_argument("--limit", type=int, default=10)

    subparsers.add_parser("runs", help="List recent runs")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    cmds = {
        "status": cmd_status,
        "dispatch-triage": cmd_dispatch_triage,
        "dispatch-build": cmd_dispatch_build,
        "dispatch-competitive": cmd_dispatch_competitive,
        "runs": cmd_runs,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()
