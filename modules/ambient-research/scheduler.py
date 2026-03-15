#!/usr/bin/env python3
"""
Ambient Research Scheduler
Run via cron on each machine. Handles task queuing and execution for the appropriate tier.

Usage:
    python3 -m modules.ambient-research.scheduler --tier 1    # Razer: queue + run Tier 1
    python3 -m modules.ambient-research.scheduler --tier 2    # Lucy: queue + run Tier 2
    python3 -m modules.ambient-research.scheduler --tier 3    # Overnight: Claude CLI synthesis
    python3 -m modules.ambient-research.scheduler --status    # Show pipeline status
    python3 -m modules.ambient-research.scheduler --seed      # Seed the Hephaestus POC stream
"""

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path.home() / ".local" / "share" / "software-of-you" / "soy.db"


def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def seed_hephaestus():
    """Seed the Hephaestus / Spec-Site POC research stream."""
    db = get_db()

    # Create the stream
    db.execute(
        """INSERT OR IGNORE INTO research_streams (name, description, keywords, linked_project_ids, priority, tier_1_cadence_hours, tier_2_cadence_hours, tier_3_cadence_hours)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "Hephaestus / Spec-Site",
            "Automated spec-site generation landscape. Design token innovation, Cloudflare ecosystem, "
            "approaches to the output homogeneity problem, competitive tools, and emerging patterns "
            "in automated web design and deployment.",
            json.dumps([
                "automated website generation",
                "spec site generator",
                "design tokens",
                "Cloudflare Workers",
                "Cloudflare Pages",
                "AI web design",
                "output homogeneity problem",
                "headless CMS automation",
                "programmatic site deployment",
                "design system automation",
            ]),
            json.dumps([]),  # Link to Specsite project_id when known
            8,  # High priority — active project
            6,
            12,
            168,
        ),
    )

    # Create initial wiki seed
    stream = db.execute("SELECT id FROM research_streams WHERE name = 'Hephaestus / Spec-Site'").fetchone()
    if stream:
        existing = db.execute("SELECT id FROM research_wikis WHERE stream_id = ?", (stream["id"],)).fetchone()
        if not existing:
            db.execute(
                """INSERT INTO research_wikis (stream_id, title, content, version, word_count)
                   VALUES (?, ?, ?, 1, 0)""",
                (
                    stream["id"],
                    "Hephaestus: Automated Spec-Site Generation",
                    HEPHAESTUS_SEED_WIKI,
                ),
            )

    db.commit()
    db.close()
    print("Hephaestus stream seeded.")


def show_status():
    """Show current pipeline status."""
    db = get_db()

    streams = db.execute("SELECT * FROM research_streams WHERE active = 1").fetchall()
    print(f"\n=== Ambient Research Pipeline Status ===")
    print(f"    Active streams: {len(streams)}\n")

    for s in streams:
        print(f"  [{s['name']}] priority={s['priority']}")
        # Task counts
        for tier in (1, 2, 3):
            counts = db.execute(
                """SELECT status, COUNT(*) as n FROM research_tasks
                   WHERE stream_id = ? AND tier = ? GROUP BY status""",
                (s["id"], tier),
            ).fetchall()
            if counts:
                parts = ", ".join(f"{r['status']}={r['n']}" for r in counts)
                print(f"    Tier {tier}: {parts}")

        # Findings
        fc = db.execute(
            "SELECT COUNT(*) as n FROM research_findings WHERE stream_id = ?", (s["id"],)
        ).fetchone()
        wc = db.execute(
            "SELECT COUNT(*) as n FROM research_wikis WHERE stream_id = ?", (s["id"],)
        ).fetchone()
        print(f"    Findings: {fc['n']} | Wiki docs: {wc['n']}")
        print()

    # Machine status
    from . import ollama_client

    print("  === Machine Status ===")
    for name in ollama_client.MACHINES:
        healthy = ollama_client.check_health(name, timeout=3)
        status = "ONLINE" if healthy else "OFFLINE"
        models = ollama_client.list_models(name, timeout=3) if healthy else []
        model_str = ", ".join(models) if models else "—"
        print(f"    {name}: {status} [{model_str}]")
    print()

    db.close()


def run_tier(tier: int):
    """Queue and run tasks for a tier."""
    from . import dispatcher

    print(f"\n=== Running Tier {tier} ===\n")

    if tier == 1:
        queued = dispatcher.queue_tier1_tasks()
        print(f"  Queued {queued} Tier 1 tasks")
    elif tier == 2:
        queued = dispatcher.queue_tier2_tasks()
        print(f"  Queued {queued} Tier 2 tasks")
    elif tier == 3:
        # Tier 3 tasks are created by the digest/synthesis commands
        print("  Checking for pending Tier 3 tasks...")

    results = dispatcher.run_pending(tier=tier, limit=10)
    print(f"\n  Completed {len(results)} tasks")

    # Log activity
    db = get_db()
    db.execute(
        """INSERT INTO activity_log (entity_type, action, description, created_at)
           VALUES ('research', 'tier_run', ?, datetime('now'))""",
        (f"Tier {tier} run: {len(results)} tasks completed",),
    )
    db.commit()
    db.close()


HEPHAESTUS_SEED_WIKI = """# Hephaestus: Automated Spec-Site Generation

## Overview

This document tracks the landscape of automated website/spec-site generation — tools, techniques,
and approaches relevant to building a system that can programmatically generate high-quality,
distinctive client-facing specification sites.

## The Core Problem: Output Homogeneity

AI-generated websites tend to look the same. The challenge is building a system that produces
sites with genuine design variety and professional quality, not "AI slop."

### Open Questions
- What design token systems allow meaningful variation without chaos?
- How do existing tools handle the homogeneity problem?
- What's the minimum viable design decision space for distinctive output?

## Competitive Landscape

*(To be populated by research sweeps)*

## Cloudflare Ecosystem

The deployment target is Cloudflare-native (Workers, Pages, CDN, KV).

### Relevant Services
- Cloudflare Pages — static site hosting with git integration
- Cloudflare Workers — serverless functions at the edge
- Cloudflare KV — key-value storage for configuration
- Cloudflare Tunnels — secure connections to origin servers

### Open Questions
- What's new in the Cloudflare deployment pipeline that could simplify automated publishing?
- Are there Cloudflare-native template/theming approaches worth exploring?

## Design Token Innovation

*(To be populated by research sweeps)*

## Technical Approaches

*(To be populated by research sweeps)*

## Adjacent Insights

*(Cross-pollination from other research streams will appear here)*

---

*Last updated: Initial seed document*
"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ambient Research Scheduler")
    parser.add_argument("--tier", type=int, choices=[1, 2, 3], help="Run tasks for this tier")
    parser.add_argument("--status", action="store_true", help="Show pipeline status")
    parser.add_argument("--seed", action="store_true", help="Seed the Hephaestus POC stream")
    args = parser.parse_args()

    if args.seed:
        seed_hephaestus()
    elif args.status:
        show_status()
    elif args.tier:
        run_tier(args.tier)
    else:
        parser.print_help()
