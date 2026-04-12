"""
Loci Layer V2 — generic graph traversal over entity_edges.

Rewrite of shared/loci.py against the next_soy schema (see
benchmarks/loci/next_soy_schema_v1.md). The V1 loci used ~10 per-entity-type
expander functions and a tree renderer; V2 replaces both with:

  1. Two generic expanders — expand_outbound(src_type, src_id) and
     expand_inbound(dst_type, dst_id) — that query the `entity_edges` table
     once per direction and resolve the neighbor rows generically.

  2. A per-entity narrative renderer (Aisha's format from the constructive
     panel): each top-level seed gets a prose-ish paragraph with a bulleted
     related list beneath it, and memory_episodes render as a distinct
     "episode card" at the top of the brief when the walk touches them.

  3. Episode-aware walk. When the BFS reaches an entity that belongs to an
     active memory_episode, the walker surfaces the episode itself as a node
     in the neighborhood (via the part_of_episode edge we materialize at
     seed time), so cross-entity framing questions (the C1 prompt: Reprise
     ↔ BATL "operator intelligence layer") are answerable from the walk.

  4. Status-aware filtering. Contacts with status ∈ {prospect, broadcast_only}
     are not walked as neighbors — the schema panel's audit established they
     dilute recall without adding signal. Inactive contacts are walked only
     when explicitly named by a seed.

  5. No more legacy linked_* parsing. The seed script (seed_next_soy.py)
     flattened linked_contacts / linked_projects into `mentions` edges at
     backfill time, so the walker doesn't need the defensive parser.

Python stdlib only. Importable as a module or runnable as a script.

Usage as a module:
    from shared.loci_v2 import assemble_context, render_narrative
    nb = assemble_context("/path/to/next_soy.db", "prep me for Jessica")
    print(render_narrative(nb))

Usage as a CLI:
    python3 shared/loci_v2.py "prep me for Jessica"
    python3 shared/loci_v2.py --soy-db /path/to/next_soy.db "agent pursuit"
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import deque
from dataclasses import dataclass, field
from typing import Optional


# ─── Entity type ↔ table map ────────────────────────────────────────
# Every entity type the walker can land on resolves to (table, pk_column,
# label_field_candidates). The label_field_candidates are tried in order
# when constructing a human-readable label; first non-null wins.

ENTITY_TYPE_MAP = {
    "contact":             ("contacts",              "id", ["name"]),
    "project":             ("projects",              "id", ["name"]),
    "project_task":        ("project_tasks",         "id", ["title"]),
    "milestone":           ("milestones",            "id", ["name"]),
    "decision":            ("decisions",             "id", ["title"]),
    "email":               ("emails",                "id", ["subject"]),
    "calendar_event":      ("calendar_events",       "id", ["title"]),
    "contact_interaction": ("contact_interactions",  "id", ["subject", "summary"]),
    "transcript":          ("transcripts",           "id", ["title"]),
    "commitment":          ("commitments",           "id", ["description"]),
    "notes_v2":            ("notes_v2",              "id", ["title"]),
    "journal_entry":       ("journal_entries",       "id", ["entry_date"]),
    "daily_log":           ("daily_logs",            "id", ["log_date"]),
    "memory_episode":      ("memory_episodes",       "id", ["title"]),
}


# Edge-type "priority" — used as a secondary sort key when budgeting the
# breadth of the walk. Structural edges rank above content edges, which rank
# above episode/conceptual edges. This only matters when a node has more
# outgoing edges than the breadth budget allows; within a priority band,
# weight and recency break ties.

EDGE_PRIORITY = {
    # Tier 1 — structural / high-information
    "client_of":          1,
    "decided_in":         1,
    "involves_contact":   1,
    "belongs_to_project": 1,
    "email_with":         1,
    "interaction_with":   1,
    "event_with":         1,
    "participated_in":    1,
    "commitment_by":      1,
    # Tier 2 — professional / personal network
    "works_at":           2,
    "employed_by":        2,
    "collaborator_on":    2,
    "colleague_of":       2,
    "family_of":          2,
    "close_friend_of":    2,
    "mentor_of":          2,
    "books_for":          2,
    "building_site_for":  2,
    "agent_of":           2,
    "represented_by":     2,
    "shareholder_of":     2,
    "owner_of":           2,
    "cc_regular_of":      2,
    "neighbor_of":        2,
    "prospect_for":       2,
    # Tier 3 — content / conceptual
    "mentions":           3,
    "part_of_episode":    3,
    "shares_framing_with": 3,
    "promoted_to":        3,
    "supersedes":         3,
    "derived_from":       3,
}


# Contact statuses that the walker should NOT traverse into during BFS.
# Seeds can still explicitly match them if the user asks; the filter only
# applies to neighbors discovered via expand_outbound / expand_inbound.
NO_WALK_CONTACT_STATUSES = frozenset({"prospect", "broadcast_only"})


# ─── Data shapes ────────────────────────────────────────────────────

@dataclass
class Node:
    """A single entity in the loci neighborhood graph."""
    entity_type: str
    entity_id: int
    data: dict
    distance: int = 0
    via_edge: Optional[str] = None            # the edge_type that brought us here
    via_parent: Optional[tuple] = None        # (entity_type, entity_id) of parent
    via_direction: Optional[str] = None       # "outbound" | "inbound" — for reverse labeling
    edge_metadata: Optional[dict] = None

    def key(self) -> tuple:
        return (self.entity_type, self.entity_id)


@dataclass
class Neighborhood:
    """A graph traversal result."""
    query: str
    seeds: list
    nodes: dict                               # dict[tuple, Node]
    episodes: dict = field(default_factory=dict)  # episode_id -> row dict
    # episode_id -> list of (entity_type, entity_id, role) tuples drawn from
    # episode_members ∩ visited nodes. Precomputed at walk time because the
    # renderer doesn't hold the DB connection and in-walk dedup can strip the
    # inbound part_of_episode edges before the render sees them.
    episode_members: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)


# ─── Database helpers ───────────────────────────────────────────────

def _open(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _safe_exec(conn, sql, params=()):
    """Execute a query, logging schema drift to stderr instead of crashing.

    Matches the v1 policy (Maya's call from the schema panel): benchmarks
    should NOT silently swallow OperationalErrors, because whole classes
    of edges can vanish and the stats block will still read success.
    """
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as e:
        print(
            f"loci_v2: schema drift: {e}\n  query: {sql}\n  params: {params}",
            file=sys.stderr,
        )
        return []


def _resolve_row(conn, entity_type, entity_id):
    """Fetch the full row for a (type, id) tuple. Returns dict or None."""
    spec = ENTITY_TYPE_MAP.get(entity_type)
    if spec is None:
        return None
    table, pk, _label_fields = spec
    rows = _safe_exec(
        conn,
        f"SELECT * FROM {table} WHERE {pk} = ?",
        (entity_id,),
    )
    return dict(rows[0]) if rows else None


# ─── Seed selection ─────────────────────────────────────────────────

_STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "what", "where", "when",
    "who", "how", "why", "do", "does", "did", "i", "my", "me", "we", "our",
    "and", "or", "but", "if", "in", "on", "at", "to", "for", "of", "with",
    "about", "from", "by", "this", "that", "these", "those", "have", "has",
    "had", "be", "been", "will", "would", "should", "could", "can", "may",
    "any", "some", "all", "no", "not", "as", "it", "its", "they", "them",
    "you", "your", "us", "ours", "theirs", "him", "her", "his", "hers",
    "just", "now", "then", "here", "there", "so", "very", "much", "many",
    "more", "most", "less", "least", "other", "another", "such", "only",
    "own", "same", "than", "too", "between", "prep",
})


def _extract_keywords(query: str) -> list:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9'-]{1,}", query.lower())
    seen = set()
    out = []
    for t in tokens:
        if t in _STOPWORDS or len(t) < 3:
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def find_seeds(
    conn: sqlite3.Connection,
    query: str,
    limit_per_table: int = 3,
    max_seeds: int = 10,
) -> list:
    """Find entities most relevant to a free-text query via match-count ranking.

    Same keyword-match-count approach as v1 — this is the "where to start"
    decision and its value is in the walk that follows. v2 updates the
    search tables for next_soy (notes_v2 instead of standalone_notes, the
    new wikilinks table for alias shortcuts, and applies the active-status
    filter so broadcast_only/prospect contacts never become seeds).
    """
    keywords = _extract_keywords(query) or [query.strip()]

    rows_by_key = {}     # (type, id) -> row dict
    hits_by_key = {}     # (type, id) -> int (distinct-keyword matches)

    def add(et, row):
        k = (et, row["id"])
        if k not in rows_by_key:
            rows_by_key[k] = dict(row)
        hits_by_key[k] = hits_by_key.get(k, 0) + 1

    for kw in keywords:
        pat = f"%{kw}%"

        # wikilinks — alias-driven shortcut. A single exact-alias hit is
        # worth strong bias, so we give wikilink matches a +2 hit count
        # (the +1 from the keyword itself and +1 for the alias landing).
        for row in _safe_exec(
            conn,
            "SELECT alias, canonical_type, canonical_id FROM wikilinks "
            "WHERE LOWER(alias) = LOWER(?)",
            (kw,),
        ):
            tgt = _resolve_row(conn, row["canonical_type"], row["canonical_id"])
            if tgt:
                k = (row["canonical_type"], row["canonical_id"])
                if k not in rows_by_key:
                    rows_by_key[k] = tgt
                hits_by_key[k] = hits_by_key.get(k, 0) + 2

        # contacts — exclude prospect/broadcast_only
        for row in _safe_exec(
            conn,
            "SELECT * FROM contacts "
            "WHERE (name LIKE ? OR company LIKE ? OR role LIKE ? OR notes LIKE ?) "
            "AND status NOT IN ('prospect', 'broadcast_only') "
            "AND merged_into_id IS NULL "
            "LIMIT ?",
            (pat, pat, pat, pat, limit_per_table),
        ):
            add("contact", row)

        for row in _safe_exec(
            conn,
            "SELECT * FROM projects "
            "WHERE (name LIKE ? OR description LIKE ?) LIMIT ?",
            (pat, pat, limit_per_table),
        ):
            add("project", row)

        for row in _safe_exec(
            conn,
            "SELECT * FROM decisions "
            "WHERE (title LIKE ? OR context LIKE ? OR decision LIKE ?) LIMIT ?",
            (pat, pat, pat, limit_per_table),
        ):
            add("decision", row)

        for row in _safe_exec(
            conn,
            "SELECT * FROM notes_v2 "
            "WHERE (title LIKE ? OR content LIKE ?) LIMIT ?",
            (pat, pat, limit_per_table),
        ):
            add("notes_v2", row)

        for row in _safe_exec(
            conn,
            "SELECT * FROM contact_interactions "
            "WHERE (subject LIKE ? OR summary LIKE ?) LIMIT ?",
            (pat, pat, limit_per_table),
        ):
            add("contact_interaction", row)

        for row in _safe_exec(
            conn,
            "SELECT * FROM emails "
            "WHERE (subject LIKE ? OR snippet LIKE ? OR body_preview LIKE ?) LIMIT ?",
            (pat, pat, pat, limit_per_table),
        ):
            add("email", row)

        # memory_episodes — titled context containers; very valuable when hit
        for row in _safe_exec(
            conn,
            "SELECT * FROM memory_episodes "
            "WHERE (title LIKE ? OR summary LIKE ?) LIMIT ?",
            (pat, pat, limit_per_table),
        ):
            add("memory_episode", row)

    sorted_keys = sorted(
        rows_by_key.keys(),
        key=lambda k: (-hits_by_key[k], k[0], k[1]),
    )
    return [
        Node(
            entity_type=k[0],
            entity_id=k[1],
            data=rows_by_key[k],
            distance=0,
        )
        for k in sorted_keys[:max_seeds]
    ]


# ─── The two generic expanders ──────────────────────────────────────

def expand_outbound(conn, node: Node, breadth: int) -> list:
    """Walk outward from `node` via entity_edges (src = node).

    One SQL call. Budgets breadth per edge_type via a window function so a
    node with lots of email_with edges doesn't crowd out a single client_of
    edge. Within an edge_type, orders by (weight DESC, created_at DESC).
    """
    src_type = node.entity_type
    src_id = node.entity_id
    rows = _safe_exec(
        conn,
        """
        SELECT * FROM (
            SELECT
                dst_type, dst_id, edge_type, weight, metadata, created_at,
                ROW_NUMBER() OVER (
                    PARTITION BY edge_type
                    ORDER BY weight DESC, created_at DESC
                ) AS rn
            FROM entity_edges
            WHERE src_type = ? AND src_id = ? AND ended_at IS NULL
        )
        WHERE rn <= ?
        ORDER BY weight DESC
        """,
        (src_type, src_id, breadth),
    )
    return _rows_to_nodes(conn, node, rows, direction="outbound")


def expand_inbound(conn, node: Node, breadth: int) -> list:
    """Walk inward to `node` via entity_edges (dst = node).

    Mirror of expand_outbound. For symmetric edges (colleague_of, family_of,
    close_friend_of) the walker sees the edge twice — once as outbound from
    one side, once as inbound from the other. De-dup at the Node level keeps
    this from causing churn.
    """
    dst_type = node.entity_type
    dst_id = node.entity_id
    rows = _safe_exec(
        conn,
        """
        SELECT * FROM (
            SELECT
                src_type, src_id, edge_type, weight, metadata, created_at,
                ROW_NUMBER() OVER (
                    PARTITION BY edge_type
                    ORDER BY weight DESC, created_at DESC
                ) AS rn
            FROM entity_edges
            WHERE dst_type = ? AND dst_id = ? AND ended_at IS NULL
        )
        WHERE rn <= ?
        ORDER BY weight DESC
        """,
        (dst_type, dst_id, breadth),
    )
    return _rows_to_nodes(conn, node, rows, direction="inbound")


def _rows_to_nodes(conn, parent_node: Node, edge_rows, direction: str) -> list:
    """Resolve edge rows to full Nodes, applying status filters."""
    out = []
    nd = parent_node.distance + 1
    parent_key = parent_node.key()

    for row in edge_rows:
        if direction == "outbound":
            nbr_type = row["dst_type"]
            nbr_id = row["dst_id"]
        else:
            nbr_type = row["src_type"]
            nbr_id = row["src_id"]

        nbr_row = _resolve_row(conn, nbr_type, nbr_id)
        if nbr_row is None:
            continue

        # Status filter: skip prospect/broadcast_only contacts on walks.
        if nbr_type == "contact" and nbr_row.get("status") in NO_WALK_CONTACT_STATUSES:
            continue

        metadata = None
        if row["metadata"]:
            try:
                metadata = json.loads(row["metadata"])
            except (TypeError, json.JSONDecodeError):
                metadata = None

        out.append(Node(
            entity_type=nbr_type,
            entity_id=nbr_id,
            data=nbr_row,
            distance=nd,
            via_edge=row["edge_type"],
            via_parent=parent_key,
            via_direction=direction,
            edge_metadata=metadata,
        ))

    # Secondary ordering by edge-type priority so higher-information edges
    # are processed first when the total budget is tight.
    out.sort(key=lambda n: EDGE_PRIORITY.get(n.via_edge, 9))
    return out


# ─── Main traversal ─────────────────────────────────────────────────

def assemble_context(
    db_path: str,
    query: str,
    seeds: Optional[list] = None,
    max_depth: int = 2,
    max_breadth_per_node: int = 5,
    max_total_nodes: int = 60,
    max_seeds: int = 10,
) -> Neighborhood:
    """Build a neighborhood graph via BFS over entity_edges.

    Same BFS skeleton as v1 — a deque, dedup by (type, id), stop when the
    total-node budget is hit — but the expand step is now two generic calls
    instead of a per-type dispatch table.

    When a memory_episode is discovered in the walk, we record it in the
    neighborhood's `episodes` dict so the renderer can surface an episode
    card separately from the per-entity paragraphs.
    """
    conn = _open(db_path)
    try:
        if seeds is None:
            seeds = find_seeds(conn, query, max_seeds=max_seeds)

        nodes = {}
        for s in seeds:
            nodes[s.key()] = s

        episodes = {}
        queue = deque(seeds)
        edges_walked = 0
        types_touched = set()

        while queue and len(nodes) < max_total_nodes:
            current = queue.popleft()
            types_touched.add(current.entity_type)

            if current.entity_type == "memory_episode":
                episodes[current.entity_id] = current.data

            if current.distance >= max_depth:
                continue

            children = expand_outbound(conn, current, max_breadth_per_node)
            children += expand_inbound(conn, current, max_breadth_per_node)

            for child in children:
                if child.key() in nodes:
                    continue
                if len(nodes) >= max_total_nodes:
                    break
                nodes[child.key()] = child
                queue.append(child)
                edges_walked += 1
                if child.entity_type == "memory_episode":
                    episodes[child.entity_id] = child.data

        # Precompute episode membership ∩ visited nodes. Done here (not in
        # render) because the renderer doesn't hold a live connection, and
        # BFS dedup can strip the inbound part_of_episode edges before the
        # renderer sees them (the member nodes get added as seeds first,
        # so the inbound-from-episode edge never overwrites their via_parent).
        episode_members = {}
        for ep_id in episodes:
            rows = _safe_exec(
                conn,
                "SELECT entity_type, entity_id, role FROM episode_members "
                "WHERE episode_id = ?",
                (ep_id,),
            )
            in_view = []
            for r in rows:
                if (r["entity_type"], r["entity_id"]) in nodes:
                    in_view.append((r["entity_type"], r["entity_id"], r["role"]))
            episode_members[ep_id] = in_view

        return Neighborhood(
            query=query,
            seeds=seeds,
            nodes=nodes,
            episodes=episodes,
            episode_members=episode_members,
            stats={
                "total_nodes": len(nodes),
                "edges_walked": edges_walked,
                "max_depth_reached": max((n.distance for n in nodes.values()), default=0),
                "entity_types_touched": sorted(types_touched),
                "seed_count": len(seeds),
                "episodes_touched": len(episodes),
            },
        )
    finally:
        conn.close()


# ─── Labeling ───────────────────────────────────────────────────────

def _label_for(node: Node) -> str:
    d = node.data
    et = node.entity_type
    if et == "contact":
        parts = [d.get("name") or "Unknown"]
        role = d.get("role")
        company = d.get("company")
        if role and company:
            parts.append(f"— {role} at {company}")
        elif role:
            parts.append(f"— {role}")
        elif company:
            parts.append(f"— {company}")
        return " ".join(parts)
    if et == "project":
        bits = [d.get("name") or "Unknown project"]
        status = d.get("status")
        if status:
            bits.append(f"[{status}]")
        if d.get("target_date"):
            bits.append(f"target {d['target_date']}")
        return " ".join(bits)
    if et == "decision":
        title = d.get("title") or "Decision"
        decided = (d.get("decided_at") or "")[:10]
        return f"Decision: {title}" + (f" ({decided})" if decided else "")
    if et == "notes_v2":
        title = d.get("title") or ((d.get("content") or "")[:60] + "…")
        return f"Note: {title}"
    if et == "contact_interaction":
        subj = d.get("subject") or "interaction"
        when = (d.get("occurred_at") or "")[:10]
        direction = d.get("direction") or "?"
        kind = d.get("type") or ""
        return f"{direction} {kind}: {subj} ({when})"
    if et == "email":
        subj = d.get("subject") or "(no subject)"
        when = (d.get("received_at") or "")[:10]
        return f"Email: {subj} ({when})"
    if et == "transcript":
        title = d.get("title") or "Transcript"
        when = (d.get("occurred_at") or "")[:10]
        return f"{title} ({when})"
    if et == "calendar_event":
        title = d.get("title") or "(untitled event)"
        when = (d.get("start_time") or "")[:16]
        loc = d.get("location")
        return f"Event: {title} ({when})" + (f" @ {loc}" if loc else "")
    if et == "project_task":
        return f"Task: {d.get('title') or ''} [{d.get('status') or ''}]"
    if et == "milestone":
        return f"Milestone: {d.get('name') or ''} [{d.get('status') or ''}]"
    if et == "commitment":
        return f"Commitment: {(d.get('description') or '')[:80]}"
    if et == "memory_episode":
        return f"Episode: {d.get('title') or ''}"
    if et == "journal_entry":
        return f"Journal {d.get('entry_date') or ''}"
    if et == "daily_log":
        return f"Daily log {d.get('log_date') or ''}"
    return f"{et}:{node.entity_id}"


def _intro_paragraph_for(node: Node) -> Optional[str]:
    """One or two sentences of seed-specific context drawn from the row's
    own fields. No synthesis, no counts the walker hasn't computed — just
    a deterministic read of the most informative field available.
    """
    d = node.data
    et = node.entity_type
    if et == "contact":
        bits = []
        if d.get("notes"):
            bits.append(d["notes"][:220])
        return " ".join(bits) if bits else None
    if et == "project":
        return (d.get("description") or "")[:300] or None
    if et == "decision":
        pieces = []
        if d.get("context"):
            pieces.append(d["context"][:200])
        if d.get("decision"):
            pieces.append(f"Decision: {d['decision'][:200]}")
        if d.get("rationale"):
            pieces.append(f"Rationale: {d['rationale'][:200]}")
        return " · ".join(pieces) if pieces else None
    if et == "notes_v2":
        return (d.get("content") or "").replace("\n", " ")[:300] or None
    if et == "memory_episode":
        return (d.get("summary") or "")[:400] or None
    return None


def _edge_label(node: Node) -> str:
    """Render the edge type + direction note for a child bullet."""
    if node.via_edge is None:
        return ""
    if node.via_direction == "inbound":
        return f"[{node.via_edge} ← reverse]"
    return f"[{node.via_edge}]"


# ─── Renderer: Aisha's per-entity narrative format ──────────────────

def render_narrative(nb: Neighborhood) -> str:
    """Render the neighborhood as a narrative brief.

    Format:
      - Optional episode cards at top (one per touched memory_episode),
        showing the episode summary + its members inside the neighborhood.
      - Per-seed sections: each seed gets a header, an intro paragraph
        derived from the row's own fields, and a bulleted list of related
        nodes grouped by edge type (or ordered by priority).
      - A short stats footer.

    Differences from v1 render_context:
      - No tree indentation with `└──` characters. Flat bullets only.
      - Edge labels are bracketed tags at line end, not inline `via` phrases.
      - Episodes are first-class, not nested under a seed.
    """
    if not nb.seeds:
        return f"No matching entities found for query: {nb.query}\n"

    children_of = {}
    for n in nb.nodes.values():
        if n.via_parent is not None:
            children_of.setdefault(n.via_parent, []).append(n)

    lines = []
    s = nb.stats
    lines.append(f"# next_soy loci brief — {nb.query!r}")
    lines.append(
        f"# {s['total_nodes']} entities · depth {s['max_depth_reached']} · "
        f"{s['edges_walked']} edges walked · "
        f"types: {', '.join(s['entity_types_touched'])}"
    )
    lines.append("")

    # Episode cards first — cross-entity framing beats per-entity detail
    if nb.episodes:
        for ep_id, ep in nb.episodes.items():
            _render_episode_card(lines, ep, ep_id, nb)

    # Per-seed sections
    rendered_seeds = set()
    for seed in nb.seeds:
        if seed.key() not in nb.nodes:
            continue
        if seed.key() in rendered_seeds:
            continue
        rendered_seeds.add(seed.key())
        # Skip episodes here — they're already rendered as cards above
        if seed.entity_type == "memory_episode":
            continue
        _render_seed_section(lines, seed, children_of, nb)

    lines.append("")
    lines.append(
        f"---\nStats: {s['total_nodes']} nodes, {s['edges_walked']} edges walked, "
        f"{len(s['entity_types_touched'])} entity types, "
        f"{s['episodes_touched']} episodes touched."
    )
    return "\n".join(lines)


def _render_episode_card(lines, ep, ep_id, nb):
    started = ep.get("started_at") or "unknown"
    ended = ep.get("ended_at")
    when_str = f"active since {started}" if not ended else f"{started} → {ended}"
    lines.append(f"## Episode: {ep.get('title') or 'Untitled'} ({when_str})")
    lines.append("")
    summary = ep.get("summary")
    if summary:
        lines.append(summary.strip())
        lines.append("")

    members = nb.episode_members.get(ep_id, [])
    if not members:
        lines.append("Members in view: (none reached within node budget)")
        lines.append("")
        return

    lines.append("Members in view:")
    for entity_type, entity_id, role in members:
        member_node = nb.nodes.get((entity_type, entity_id))
        if member_node is None:
            continue
        role_tag = f" ({role})" if role else ""
        lines.append(f"  - {_label_for(member_node)}{role_tag}")
    lines.append("")


def _render_seed_section(lines, seed, children_of, nb):
    header = f"## {_label_for(seed)}"
    lines.append(header)
    intro = _intro_paragraph_for(seed)
    if intro:
        lines.append("")
        lines.append(intro)
    lines.append("")

    # Walk children + grandchildren; flat bullets ordered by priority
    related = _collect_related(seed, children_of)
    if not related:
        lines.append("  (no related entities in budget)")
    else:
        lines.append("Related:")
        # Group by priority band for readability
        by_priority = {}
        for child in related:
            pri = EDGE_PRIORITY.get(child.via_edge, 9)
            by_priority.setdefault(pri, []).append(child)
        for pri in sorted(by_priority):
            for child in by_priority[pri]:
                lines.append(f"  - {_label_for(child)} {_edge_label(child)}")
                # Second-line detail for high-value types
                detail = _intro_paragraph_for(child)
                if detail and child.entity_type in {"decision", "notes_v2"}:
                    snippet = detail[:160]
                    lines.append(f"      {snippet}")
    lines.append("")


def _collect_related(seed: Node, children_of: dict, limit: int = 15) -> list:
    """DFS out from a seed, collecting direct + grandchild nodes.

    Returns up to `limit` descendants, ordered by BFS distance then by
    edge priority. Episodes are skipped — they render as their own card.
    """
    out = []
    seen = {seed.key()}
    queue = deque([seed])
    while queue and len(out) < limit:
        current = queue.popleft()
        for child in children_of.get(current.key(), []):
            if child.key() in seen:
                continue
            if child.entity_type == "memory_episode":
                continue
            seen.add(child.key())
            out.append(child)
            queue.append(child)
            if len(out) >= limit:
                break
    return out


# ─── CLI ────────────────────────────────────────────────────────────

def _default_db_path() -> str:
    # Prefer next_soy.db if it's built, fall back to real soy.db so v2 can
    # be run ad-hoc in transitional states.
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    next_soy = os.path.join(
        here, "benchmarks", "loci", "next_soy_schema", "next_soy.db"
    )
    if os.path.exists(next_soy):
        return next_soy
    return os.path.expanduser("~/.local/share/software-of-you/soy.db")


def _main():
    parser = argparse.ArgumentParser(
        description="Loci V2 — narrative brief from next_soy.db"
    )
    parser.add_argument("query", nargs="+", help="Free-text query")
    parser.add_argument(
        "--soy-db", default=None,
        help="Path to next_soy.db (default: benchmarks/loci/next_soy_schema/next_soy.db)",
    )
    parser.add_argument("--depth", type=int, default=2)
    parser.add_argument("--breadth", type=int, default=5)
    parser.add_argument("--total", type=int, default=60)
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument(
        "--stats-json", action="store_true",
        help="Append a raw JSON stats block after the narrative",
    )
    args = parser.parse_args()

    query = " ".join(args.query)
    db_path = args.soy_db or _default_db_path()
    if not os.path.exists(db_path):
        print(f"Database not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    nb = assemble_context(
        db_path, query,
        max_depth=args.depth,
        max_breadth_per_node=args.breadth,
        max_total_nodes=args.total,
        max_seeds=args.seeds,
    )
    print(render_narrative(nb))
    if args.stats_json:
        print("\n--- stats ---")
        print(json.dumps(nb.stats, indent=2))


if __name__ == "__main__":
    _main()
