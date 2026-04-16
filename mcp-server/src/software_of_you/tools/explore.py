"""Explore tool — graph-traversal context assembly via loci_v2.

The "friend" in the librarian-and-friend routing architecture. Walks the
entity_edges graph from a free-text query outward through typed edges,
surfaces memory_episodes when the walk touches episode members, and renders
a narrative context brief.

Complements the existing `search` tool (the "librarian"), which handles
direct fact lookups. The model chooses between them based on the question:
- "What did I decide about X?" → search (fact lookup)
- "What threads connect X to Y?" → explore (graph walk)
- "Prep me for Z" → explore (neighborhood assembly)

Now reads from the production soy.db directly — the V2 tables (entity_edges,
memory_episodes, etc.) were added via migrate_to_v2.py on 2026-04-12.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from software_of_you.db import DB_PATH

# Add the shared/ directory to the import path so we can import loci_v2
_REPO_ROOT = Path(__file__).resolve().parents[4]  # mcp-server/src/software_of_you/tools → repo root
_SHARED_DIR = _REPO_ROOT / "shared"
if str(_SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_DIR))

# Usage log: JSONL file in the loci-journey repo
_LOG_PATH = Path.home() / "wkspaces" / "loci-journey" / "explore_usage.jsonl"


def register(server: FastMCP) -> None:
    @server.tool()
    def explore(query: str) -> dict:
        """Walk the relationship graph to discover connections, prep context, or surface patterns.

        Use this tool when the question involves connecting things, prepping for
        a meeting or work session, finding what's changed or fallen off, or
        understanding how different parts of the user's life relate.

        Best for:
        - "What threads connect X to Y?"
        - "Prep me for recording with Z"
        - "What's fallen off my radar?"
        - "Where am I most likely overcommitting?"
        - "What's the relationship between X and Y?"

        For direct fact lookups ("what's X's email", "what did I decide about Y"),
        use the search tool instead — it's faster and more precise for those.

        Args:
            query: A natural-language question or phrase to explore
        """
        if not query:
            return {"error": "A query is required."}

        db_path = str(DB_PATH)
        if not os.path.exists(db_path):
            return {"error": "soy.db not found."}

        try:
            from loci_v2 import assemble_context, render_narrative
        except ImportError as e:
            return {"error": f"Failed to import loci_v2: {e}"}

        try:
            neighborhood = assemble_context(db_path, query)
            narrative = render_narrative(neighborhood)
        except Exception as e:
            return {"error": f"Explore failed: {type(e).__name__}: {e}"}

        stats = neighborhood.stats
        episodes_touched = list(neighborhood.episodes.keys())
        now = datetime.now(timezone.utc)

        # Freshness signal — the consuming model can hedge current-state claims
        freshness_line = f"\n\n---\n*Context assembled {now.strftime('%Y-%m-%d %H:%M UTC')} from soy.db. Verify current-state claims against live data.*"
        narrative_with_freshness = narrative + freshness_line

        result_stats = {
            "total_nodes": stats.get("total_nodes", 0),
            "edges_walked": stats.get("edges_walked", 0),
            "max_depth": stats.get("max_depth_reached", 0),
            "entity_types": stats.get("entity_types_touched", []),
            "seed_count": stats.get("seed_count", 0),
            "episodes_touched": len(episodes_touched),
        }

        # Log usage for later survey
        _log_usage(query, result_stats, len(narrative), now)

        return {
            "narrative": narrative_with_freshness,
            "stats": result_stats,
            "_context": {
                "tool_role": "This is the 'friend' — associative graph walk. "
                             "For direct fact lookups, use the search tool instead.",
                "data_source": "soy.db (production, V2 tables applied)",
                "checkpoint_at": now.isoformat(),
                "suggestion": _suggest_next(neighborhood),
            },
        }


def _suggest_next(neighborhood) -> str:
    """Generate a contextual next-action suggestion based on what the walk found."""
    stats = neighborhood.stats
    episodes = neighborhood.episodes

    if episodes:
        ep_titles = [ep.get("title", "untitled") for ep in episodes.values()]
        return (
            f"The walk touched {len(episodes)} episode(s): {', '.join(ep_titles)}. "
            f"You could ask about any of these for deeper context."
        )

    if stats.get("total_nodes", 0) < 5:
        return (
            "The walk found limited context. This might be a better fit for "
            "the search tool (direct lookup), or the data may be sparse for this query."
        )

    types = stats.get("entity_types_touched", [])
    if "decision" in types:
        return "Decisions surfaced in the walk — ask about specific ones for rationale and outcome."
    if "email" in types:
        return "Emails surfaced — ask about specific threads for detail."

    return "Explore deeper by asking about specific entities or connections that appeared."


def _log_usage(query, stats, narrative_len, timestamp):
    """Append a usage record to the JSONL log."""
    try:
        record = {
            "ts": timestamp.isoformat(),
            "query": query,
            "stats": stats,
            "narrative_chars": narrative_len,
        }
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_LOG_PATH, "a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass  # Logging is supplementary — never break the tool
