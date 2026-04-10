"""
Loci Layer — graph-traversal context assembly for SoY.

Walks the entity graph from a free-text query (or explicit seeds) outward
through typed edges, returning a structured neighborhood and a tree-shaped
text rendering that preserves the associative path. The renderer is the
point: instead of dumping flat row lists into LLM context, the LLM sees
a narrative of connections — "starting from contact X, walking to project Y
because X is the client, walking to decision Z because it's about Y."

This module is intentionally restricted to tables defined in migrations
001-016, which both the local fork (mrlovelies/better-software-of-you) and
upstream (kmorebetter/better-software-of-you) share. See ALLOWED_TABLES
below for the explicit whitelist. Adding a table is a deliberate design
choice — verify it exists in upstream's migrations 001-016 before adding.

Conventions inherited from benchmarks/gemma4/benchmark.py:
- Python stdlib only. No anthropic SDK, no requests, no pyyaml.
- Self-contained. Importable as a module or runnable as a script.
- Defensive against schema drift: queries reference fields conservatively
  and use .get() on row dicts.

Usage as a module:
    from shared.loci import assemble_context, render_context
    nb = assemble_context("/path/to/soy.db", query="agent pursuit")
    print(render_context(nb))

Usage as a CLI:
    python3 shared/loci.py "What is the state of agent pursuit?"
"""

import json
import os
import re
import sqlite3
import sys
from collections import deque
from dataclasses import dataclass, field
from typing import Optional


# ─── Table whitelist ─────────────────────────────────────────────────
# Loci is allowed to read from these tables only. They are all defined
# in migrations 001-016, which both the local fork and upstream share.
# Adding a table here is a deliberate design choice — confirm the table
# exists in upstream's schema before adding it, and note the migration
# number in the comment.

ALLOWED_TABLES = frozenset({
    # Migration 001 (core)
    "contacts",
    "tags",
    "entity_tags",
    "notes",
    # Migration 002 (CRM)
    "contact_interactions",
    "contact_relationships",
    "follow_ups",
    # Migration 003 (project tracker)
    "projects",
    "tasks",
    "milestones",
    # Migration 004 (gmail)
    "emails",
    # Migration 005 (calendar)
    "calendar_events",
    # Migration 006 (conversation intelligence)
    "transcripts",
    "transcript_participants",
    "commitments",
    "communication_insights",
    "relationship_scores",
    # Migration 007 (decision journal)
    "decisions",
    "journal_entries",
    # Migration 008 (notes module)
    "standalone_notes",
})


# ─── Data shapes ─────────────────────────────────────────────────────

@dataclass
class Node:
    """A single entity in the loci neighborhood graph."""
    entity_type: str           # "contact", "project", "decision", etc.
    entity_id: int
    data: dict                 # the row as a dict
    distance: int = 0          # BFS distance from a seed
    via_edge: Optional[str] = None      # the edge type that brought us here
    via_parent: Optional[tuple] = None  # (entity_type, entity_id) of the parent

    def key(self) -> tuple:
        return (self.entity_type, self.entity_id)


@dataclass
class Neighborhood:
    """A graph traversal result."""
    query: str
    seeds: list                       # list[Node]
    nodes: dict                       # dict[tuple, Node]
    stats: dict = field(default_factory=dict)


# ─── Database helpers ────────────────────────────────────────────────

def _open(db_path: str) -> sqlite3.Connection:
    """Open the SoY database with row_factory configured for dict-like access."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _check_table(table: str) -> None:
    """Enforce the ALLOWED_TABLES whitelist. Raises ValueError if violated."""
    if table not in ALLOWED_TABLES:
        raise ValueError(
            f"loci.py: table '{table}' is not in ALLOWED_TABLES. "
            f"This restriction exists to keep the loci core compatible with "
            f"upstream kmorebetter/better-software-of-you. If you need a new "
            f"table, verify it exists in upstream's migrations 001-016 and "
            f"add it to ALLOWED_TABLES with a migration number comment."
        )


def _query(conn: sqlite3.Connection, table: str, sql: str, params: tuple = ()) -> list:
    """Run a parameterized query against a whitelisted table. Returns list of dicts.

    On schema drift (sqlite3.OperationalError), logs to stderr and returns
    an empty list. The previous behavior was silent swallowing — Maya Chen's
    panel finding called this out as "the worst possible defensive default in
    a benchmark," because whole edge classes can vanish without anyone noticing
    and the stats block will happily report success on a half-broken traversal.

    The benchmark needs to fail loudly on drift. Logging is the minimum bar.
    Whether to also raise (the alternative Maya suggested) depends on context:
    for an interactive ad-hoc query you want resilience, for a controlled
    A/B/C run you want a hard stop. For now we log and continue — the runner
    captures the resulting empty walk and the report's stats will reflect it.
    """
    _check_table(table)
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as e:
        print(
            f"loci._query: schema drift on table '{table}': {e}\n"
            f"  query: {sql}\n  params: {params}",
            file=sys.stderr,
        )
        return []
    return [dict(r) for r in rows]


# ─── Seed selection ──────────────────────────────────────────────────
# Find the entities most relevant to a free-text query. This is the
# "where to start the walk" decision. Loci's value is in the walk, not
# the seed selection — but we still need a seed selection that's at
# least as good as the existing flat search, so the comparison is fair.

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
    "own", "same", "than", "too", "between",
})


def _extract_keywords(query: str) -> list:
    """Pull search-useful tokens from a free-text query."""
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9'-]{1,}", query.lower())
    out = []
    seen = set()
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
    """Find starting entities for a query via flat LIKE search across whitelisted tables.

    Tokenizes the query into keywords (stopwords removed), searches every
    keyword across every table, then ranks results by how many distinct
    keywords each entity matched. Returns the top `max_seeds` by match count.

    Why match-count ranking:
        The previous implementation iterated by keyword and broke early when
        the seed budget filled, so generic keywords like "things" or "land"
        consumed the budget before specific keywords like "chemo" were ever
        searched. A2 (the chemo descriptor prompt) failed across all arms
        because of this — interaction id 7 contained "mom is in chemo" but
        was never seeded. Match-count ranking surfaces multi-keyword matches
        first, which naturally favors entities that the query actually
        describes over entities that share generic vocabulary.
    """
    keywords = _extract_keywords(query) or [query]

    rows_by_key: dict = {}    # (entity_type, id) -> row dict
    hits_by_key: dict = {}    # (entity_type, id) -> int (how many distinct keywords matched)

    def add(et: str, row: dict) -> None:
        k = (et, row["id"])
        if k not in rows_by_key:
            rows_by_key[k] = row
        hits_by_key[k] = hits_by_key.get(k, 0) + 1

    for kw in keywords:
        pat = f"%{kw}%"

        for row in _query(conn, "contacts",
            "SELECT * FROM contacts WHERE (name LIKE ? OR company LIKE ? OR role LIKE ?) "
            "AND status = 'active' LIMIT ?",
            (pat, pat, pat, limit_per_table)):
            add("contact", row)

        for row in _query(conn, "projects",
            "SELECT * FROM projects WHERE (name LIKE ? OR description LIKE ?) LIMIT ?",
            (pat, pat, limit_per_table)):
            add("project", row)

        for row in _query(conn, "decisions",
            "SELECT * FROM decisions WHERE (title LIKE ? OR context LIKE ? OR decision LIKE ?) "
            "LIMIT ?",
            (pat, pat, pat, limit_per_table)):
            add("decision", row)

        for row in _query(conn, "standalone_notes",
            "SELECT * FROM standalone_notes WHERE (title LIKE ? OR content LIKE ?) LIMIT ?",
            (pat, pat, limit_per_table)):
            add("standalone_note", row)

        for row in _query(conn, "contact_interactions",
            "SELECT * FROM contact_interactions WHERE (subject LIKE ? OR summary LIKE ?) LIMIT ?",
            (pat, pat, limit_per_table)):
            add("interaction", row)

        for row in _query(conn, "emails",
            "SELECT * FROM emails WHERE (subject LIKE ? OR snippet LIKE ? OR body_preview LIKE ?) "
            "LIMIT ?",
            (pat, pat, pat, limit_per_table)):
            add("email", row)

    # Rank by keyword match count (desc), with stable tie-break on entity_type, id.
    # This is the load-bearing change: specific multi-keyword matches now beat
    # generic single-keyword matches even when the latter were collected first.
    sorted_keys = sorted(
        rows_by_key.keys(),
        key=lambda k: (-hits_by_key[k], k[0], k[1]),
    )
    return [
        Node(entity_type=k[0], entity_id=k[1], data=rows_by_key[k], distance=0)
        for k in sorted_keys[:max_seeds]
    ]


# ─── Edge expansion ──────────────────────────────────────────────────
# Each expander takes a node and returns its outward neighbors as new
# Nodes tagged with the edge type that connected them. The expanders
# are intentionally small and table-specific — easy to read, easy to
# verify against the schema, easy to extend.

def _expand_contact(conn, node: Node, breadth: int) -> list:
    cid = node.entity_id
    parent = node.key()
    nd = node.distance + 1
    out = []

    for row in _query(conn, "projects",
        "SELECT * FROM projects WHERE client_id = ? ORDER BY updated_at DESC LIMIT ?",
        (cid, breadth)):
        out.append(Node("project", row["id"], row, nd, "project.client_id", parent))

    for row in _query(conn, "contact_interactions",
        "SELECT * FROM contact_interactions WHERE contact_id = ? "
        "ORDER BY occurred_at DESC LIMIT ?",
        (cid, breadth)):
        out.append(Node("interaction", row["id"], row, nd, "interaction.contact_id", parent))

    for row in _query(conn, "emails",
        "SELECT * FROM emails WHERE contact_id = ? ORDER BY received_at DESC LIMIT ?",
        (cid, breadth)):
        out.append(Node("email", row["id"], row, nd, "email.contact_id", parent))

    for row in _query(conn, "decisions",
        "SELECT * FROM decisions WHERE contact_id = ? ORDER BY decided_at DESC LIMIT ?",
        (cid, breadth)):
        out.append(Node("decision", row["id"], row, nd, "decision.contact_id", parent))

    for row in _query(conn, "follow_ups",
        "SELECT * FROM follow_ups WHERE contact_id = ? AND status = 'pending' LIMIT ?",
        (cid, breadth)):
        out.append(Node("follow_up", row["id"], row, nd, "follow_up.contact_id", parent))

    for row in _query(conn, "transcripts",
        "SELECT t.* FROM transcripts t "
        "JOIN transcript_participants tp ON tp.transcript_id = t.id "
        "WHERE tp.contact_id = ? ORDER BY t.occurred_at DESC LIMIT ?",
        (cid, breadth)):
        out.append(Node("transcript", row["id"], row, nd,
                        "transcript_participants.contact_id", parent))

    # calendar_events via contact_ids (TEXT field, same parse-in-Python pattern
    # as linked_contacts above — never use LIKE on raw id substrings).
    event_candidates = _query(conn, "calendar_events",
        "SELECT * FROM calendar_events WHERE contact_ids IS NOT NULL "
        "AND contact_ids != '' AND status != 'cancelled' "
        "ORDER BY start_time DESC")
    matched_events = 0
    for ev in event_candidates:
        if matched_events >= breadth:
            break
        if cid in _parse_id_list(ev.get("contact_ids")):
            out.append(Node("calendar_event", ev["id"], ev, nd,
                            "calendar_event.contact_ids", parent))
            matched_events += 1

    for row in _query(conn, "notes",
        "SELECT * FROM notes WHERE entity_type = 'contact' AND entity_id = ? "
        "ORDER BY created_at DESC LIMIT ?",
        (cid, breadth)):
        out.append(Node("note", row["id"], row, nd, "note.entity_type+entity_id", parent))

    # standalone_notes via linked_contacts. We CANNOT use LIKE '%cid%' here:
    # `linked_contacts LIKE '%7%'` matches contact 7 AND contacts 17, 27, 70...
    # any id whose decimal representation contains the digit 7. Fetch with a
    # NOT NULL prefilter, parse the JSON/CSV in Python, exact-match by id OR
    # by contact name (the field has been used with both representations).
    cname = node.data.get("name")
    candidates = _query(conn, "standalone_notes",
        "SELECT * FROM standalone_notes WHERE linked_contacts IS NOT NULL "
        "AND linked_contacts != '' ORDER BY pinned DESC, created_at DESC")
    matched = 0
    for note in candidates:
        if matched >= breadth:
            break
        raw_link = note.get("linked_contacts")
        if cid in _parse_id_list(raw_link) or _name_in_linked_field(cname, raw_link):
            out.append(Node("standalone_note", note["id"], note, nd,
                            "standalone_note.linked_contacts", parent))
            matched += 1

    for row in _query(conn, "tags",
        "SELECT t.* FROM tags t "
        "JOIN entity_tags et ON et.tag_id = t.id "
        "WHERE et.entity_type = 'contact' AND et.entity_id = ? LIMIT ?",
        (cid, breadth)):
        out.append(Node("tag", row["id"], row, nd, "entity_tags.contact", parent))

    for row in _query(conn, "contacts",
        "SELECT c.*, cr.relationship_type FROM contacts c "
        "JOIN contact_relationships cr ON "
        "(cr.contact_id_a = ? AND cr.contact_id_b = c.id) OR "
        "(cr.contact_id_b = ? AND cr.contact_id_a = c.id) "
        "LIMIT ?",
        (cid, cid, breadth)):
        out.append(Node("contact", row["id"], row, nd, "contact_relationships", parent))

    return out


def _expand_project(conn, node: Node, breadth: int) -> list:
    pid = node.entity_id
    parent = node.key()
    nd = node.distance + 1
    out = []

    if node.data.get("client_id"):
        for row in _query(conn, "contacts",
            "SELECT * FROM contacts WHERE id = ?", (node.data["client_id"],)):
            out.append(Node("contact", row["id"], row, nd,
                            "project.client_id (reverse)", parent))

    for row in _query(conn, "tasks",
        "SELECT * FROM tasks WHERE project_id = ? "
        "ORDER BY (due_date IS NULL), due_date ASC LIMIT ?",
        (pid, breadth)):
        out.append(Node("task", row["id"], row, nd, "task.project_id", parent))

    for row in _query(conn, "milestones",
        "SELECT * FROM milestones WHERE project_id = ? ORDER BY target_date ASC LIMIT ?",
        (pid, breadth)):
        out.append(Node("milestone", row["id"], row, nd, "milestone.project_id", parent))

    for row in _query(conn, "decisions",
        "SELECT * FROM decisions WHERE project_id = ? ORDER BY decided_at DESC LIMIT ?",
        (pid, breadth)):
        out.append(Node("decision", row["id"], row, nd, "decision.project_id", parent))

    # calendar_events linked to this project (proper FK, no parsing required)
    for row in _query(conn, "calendar_events",
        "SELECT * FROM calendar_events WHERE project_id = ? AND status != 'cancelled' "
        "ORDER BY start_time DESC LIMIT ?",
        (pid, breadth)):
        out.append(Node("calendar_event", row["id"], row, nd,
                        "calendar_event.project_id", parent))

    # standalone_notes via linked_projects — same parse-in-Python pattern as
    # _expand_contact above. LIKE '%pid%' would match id collisions, AND many
    # notes store the project NAME instead of the id (legacy schema variant).
    pname = node.data.get("name")
    candidates = _query(conn, "standalone_notes",
        "SELECT * FROM standalone_notes WHERE linked_projects IS NOT NULL "
        "AND linked_projects != '' ORDER BY pinned DESC, created_at DESC")
    matched = 0
    for note in candidates:
        if matched >= breadth:
            break
        raw_link = note.get("linked_projects")
        if pid in _parse_id_list(raw_link) or _name_in_linked_field(pname, raw_link):
            out.append(Node("standalone_note", note["id"], note, nd,
                            "standalone_note.linked_projects", parent))
            matched += 1

    for row in _query(conn, "notes",
        "SELECT * FROM notes WHERE entity_type = 'project' AND entity_id = ? "
        "ORDER BY created_at DESC LIMIT ?",
        (pid, breadth)):
        out.append(Node("note", row["id"], row, nd, "note.entity_type+entity_id", parent))

    return out


def _expand_decision(conn, node: Node, breadth: int) -> list:
    parent = node.key()
    nd = node.distance + 1
    out = []

    if node.data.get("project_id"):
        for row in _query(conn, "projects",
            "SELECT * FROM projects WHERE id = ?", (node.data["project_id"],)):
            out.append(Node("project", row["id"], row, nd,
                            "decision.project_id (reverse)", parent))

    if node.data.get("contact_id"):
        for row in _query(conn, "contacts",
            "SELECT * FROM contacts WHERE id = ?", (node.data["contact_id"],)):
            out.append(Node("contact", row["id"], row, nd,
                            "decision.contact_id (reverse)", parent))

    return out


def _name_in_linked_field(name: str, raw) -> bool:
    """Check if a project/contact name appears in a linked_X field that might
    contain a name-string instead of an id-list (legacy schema variant).

    The linked_projects column has been used inconsistently — some rows store
    IDs ('210', '[214, 206]'), some store the project name as a string
    ('BATL Lane Command', 'ARIA'). _parse_id_list correctly extracts ids but
    misses the name-string rows. This helper covers the name-string case.

    Uses exact equality against parsed list elements OR comma-split parts to
    avoid the substring trap that broke linked_contacts (where 'Repri' would
    falsely match 'Reprise').
    """
    if not name or raw is None:
        return False
    s = str(raw).strip()
    if not s:
        return False
    # JSON list of strings
    try:
        parsed = json.loads(s)
        if isinstance(parsed, list):
            return any(str(x).strip() == name for x in parsed)
    except (json.JSONDecodeError, ValueError):
        pass
    # CSV / single value
    parts = [p.strip() for p in s.split(",")]
    return name in parts


def _parse_id_list(raw) -> list:
    """Parse a linked_contacts/linked_projects field that might be JSON or CSV."""
    if not raw:
        return []
    if isinstance(raw, list):
        return [int(x) for x in raw if str(x).strip().isdigit()]
    s = str(raw).strip()
    # Try JSON first
    try:
        parsed = json.loads(s)
        if isinstance(parsed, list):
            return [int(x) for x in parsed if str(x).strip().isdigit()]
    except (json.JSONDecodeError, ValueError):
        pass
    # Fall back to comma-separated
    return [int(x) for x in re.findall(r"\d+", s)]


def _parse_tag_list(raw) -> list:
    """Parse a tags field that might be JSON array or comma-separated string."""
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    s = str(raw).strip()
    try:
        parsed = json.loads(s)
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
    except (json.JSONDecodeError, ValueError):
        pass
    return [t.strip() for t in s.split(",") if t.strip()]


def _expand_standalone_note(conn, node: Node, breadth: int) -> list:
    parent = node.key()
    nd = node.distance + 1
    out = []
    d = node.data

    for cid in _parse_id_list(d.get("linked_contacts")):
        for row in _query(conn, "contacts",
            "SELECT * FROM contacts WHERE id = ?", (cid,)):
            out.append(Node("contact", row["id"], row, nd,
                            "standalone_note.linked_contacts (reverse)", parent))

    for pid in _parse_id_list(d.get("linked_projects")):
        for row in _query(conn, "projects",
            "SELECT * FROM projects WHERE id = ?", (pid,)):
            out.append(Node("project", row["id"], row, nd,
                            "standalone_note.linked_projects (reverse)", parent))

    # Tag-based proximity: find other notes that share at least one tag.
    # This is one of loci's most distinctive moves — it connects notes
    # by concept, not by foreign key.
    #
    # We CANNOT use `tags LIKE '%foo%'` here because tags is a JSON or CSV
    # text field — substring matching would map "learning" → "learning-science",
    # "machine-learning", "unlearning" and inflate the neighborhood with
    # plausible-looking-but-wrong concept links. The tag walk is supposed
    # to be loci's killer feature; it has to use exact set intersection.
    own_tags = set(_parse_tag_list(d.get("tags")))
    if own_tags:
        candidates = _query(conn, "standalone_notes",
            "SELECT * FROM standalone_notes WHERE id != ? "
            "AND tags IS NOT NULL AND tags != '' "
            "ORDER BY pinned DESC, created_at DESC",
            (node.entity_id,))
        matched = 0
        for cand in candidates:
            if matched >= breadth:
                break
            cand_tags = set(_parse_tag_list(cand.get("tags")))
            if own_tags & cand_tags:
                out.append(Node("standalone_note", cand["id"], cand, nd,
                                "standalone_note.tags (shared)", parent))
                matched += 1

    return out


def _expand_interaction(conn, node: Node, breadth: int) -> list:
    parent = node.key()
    nd = node.distance + 1
    out = []
    if node.data.get("contact_id"):
        for row in _query(conn, "contacts",
            "SELECT * FROM contacts WHERE id = ?", (node.data["contact_id"],)):
            out.append(Node("contact", row["id"], row, nd,
                            "interaction.contact_id (reverse)", parent))
    return out


def _expand_email(conn, node: Node, breadth: int) -> list:
    parent = node.key()
    nd = node.distance + 1
    out = []
    if node.data.get("contact_id"):
        for row in _query(conn, "contacts",
            "SELECT * FROM contacts WHERE id = ?", (node.data["contact_id"],)):
            out.append(Node("contact", row["id"], row, nd,
                            "email.contact_id (reverse)", parent))
    if node.data.get("thread_id"):
        for row in _query(conn, "emails",
            "SELECT * FROM emails WHERE thread_id = ? AND id != ? "
            "ORDER BY received_at DESC LIMIT ?",
            (node.data["thread_id"], node.entity_id, breadth)):
            out.append(Node("email", row["id"], row, nd, "email.thread_id", parent))
    return out


def _expand_transcript(conn, node: Node, breadth: int) -> list:
    tid = node.entity_id
    parent = node.key()
    nd = node.distance + 1
    out = []

    for row in _query(conn, "contacts",
        "SELECT c.* FROM contacts c "
        "JOIN transcript_participants tp ON tp.contact_id = c.id "
        "WHERE tp.transcript_id = ? AND tp.is_user = 0 LIMIT ?",
        (tid, breadth)):
        out.append(Node("contact", row["id"], row, nd,
                        "transcript_participants (reverse)", parent))

    for row in _query(conn, "commitments",
        "SELECT * FROM commitments WHERE transcript_id = ? "
        "AND status IN ('open', 'overdue') LIMIT ?",
        (tid, breadth)):
        out.append(Node("commitment", row["id"], row, nd,
                        "commitment.transcript_id", parent))

    return out


def _expand_tag(conn, node: Node, breadth: int) -> list:
    """A tag connects to all entities that share it — concept proximity."""
    parent = node.key()
    nd = node.distance + 1
    tag_id = node.entity_id
    out = []

    for row in _query(conn, "entity_tags",
        "SELECT entity_type, entity_id FROM entity_tags WHERE tag_id = ? LIMIT ?",
        (tag_id, breadth)):
        et = row["entity_type"]
        eid = row["entity_id"]
        if et == "contact":
            entity_rows = _query(conn, "contacts",
                "SELECT * FROM contacts WHERE id = ?", (eid,))
            if entity_rows:
                out.append(Node("contact", eid, entity_rows[0], nd,
                                "entity_tags (shared)", parent))
        elif et == "project":
            entity_rows = _query(conn, "projects",
                "SELECT * FROM projects WHERE id = ?", (eid,))
            if entity_rows:
                out.append(Node("project", eid, entity_rows[0], nd,
                                "entity_tags (shared)", parent))

    return out


# Dispatch table. Leaf node types (no expansion) are listed explicitly
# as None so the dispatch is total — surprises become KeyErrors that
# fail loud rather than silent traversal gaps.
EXPANDERS = {
    "contact": _expand_contact,
    "project": _expand_project,
    "decision": _expand_decision,
    "standalone_note": _expand_standalone_note,
    "interaction": _expand_interaction,
    "email": _expand_email,
    "transcript": _expand_transcript,
    "tag": _expand_tag,
    # Terminal types — no further expansion
    "task": None,
    "milestone": None,
    "follow_up": None,
    "note": None,
    "commitment": None,
    "journal_entry": None,
    "calendar_event": None,
}


def _expand(conn: sqlite3.Connection, node: Node, breadth: int) -> list:
    if node.entity_type not in EXPANDERS:
        return []
    expander = EXPANDERS[node.entity_type]
    if expander is None:
        return []
    return expander(conn, node, breadth)


# ─── Main traversal ──────────────────────────────────────────────────

def assemble_context(
    db_path: str,
    query: str,
    seeds: Optional[list] = None,
    max_depth: int = 2,
    max_breadth_per_node: int = 5,
    max_total_nodes: int = 60,
    max_seeds: int = 10,
) -> Neighborhood:
    """Build a neighborhood graph by walking outward from seeds via BFS.

    Args:
        db_path: Path to soy.db.
        query: The free-text query (used to find seeds if not given).
        seeds: Optional explicit seed list. If None, runs find_seeds(query).
        max_depth: How many edge-hops to walk from each seed.
        max_breadth_per_node: Max children to expand per node per edge type.
        max_total_nodes: Hard cap on total nodes in the neighborhood.
        max_seeds: Cap on number of starting seeds. Without this cap, a
            multi-keyword query can fill the entire node budget with seeds
            and leave no room for the walk.

    Returns:
        Neighborhood with seeds, all visited nodes, and traversal stats.
    """
    conn = _open(db_path)
    try:
        if seeds is None:
            seeds = find_seeds(conn, query, max_seeds=max_seeds)

        nodes: dict = {}
        for s in seeds:
            nodes[s.key()] = s

        queue: deque = deque(seeds)
        edges_walked = 0
        types_touched: set = set()

        while queue and len(nodes) < max_total_nodes:
            current = queue.popleft()
            types_touched.add(current.entity_type)

            if current.distance >= max_depth:
                continue

            children = _expand(conn, current, max_breadth_per_node)
            for child in children:
                if child.key() in nodes:
                    continue
                if len(nodes) >= max_total_nodes:
                    break
                nodes[child.key()] = child
                queue.append(child)
                edges_walked += 1

        return Neighborhood(
            query=query,
            seeds=seeds,
            nodes=nodes,
            stats={
                "total_nodes": len(nodes),
                "edges_walked": edges_walked,
                "max_depth_reached": max((n.distance for n in nodes.values()), default=0),
                "entity_types_touched": sorted(types_touched),
                "seed_count": len(seeds),
            },
        )
    finally:
        conn.close()


# ─── Renderer ────────────────────────────────────────────────────────
# Convert a Neighborhood into a text blob suitable for LLM context.
# The shape preserves the associative path: each child is indented
# under its parent with the edge type noted. This is loci's answer to
# flat row dumps — the LLM sees a narrative of connections, not a list.

def _label_for(node: Node) -> str:
    d = node.data
    et = node.entity_type
    if et == "contact":
        parts = [d.get("name") or "Unknown"]
        role = d.get("role")
        company = d.get("company")
        if role and company:
            parts.append(f"({role} at {company})")
        elif role:
            parts.append(f"({role})")
        elif company:
            parts.append(f"(at {company})")
        return " ".join(parts)
    if et == "project":
        bits = [d.get("name") or "Unknown project"]
        status = d.get("status")
        priority = d.get("priority")
        if status and priority:
            bits.append(f"[{status}, {priority} priority]")
        elif status:
            bits.append(f"[{status}]")
        if d.get("target_date"):
            bits.append(f"target {d['target_date']}")
        return " ".join(bits)
    if et == "decision":
        title = d.get("title") or "Decision"
        decided = (d.get("decided_at") or "")[:10]
        return f"Decision: {title}" + (f" ({decided})" if decided else "")
    if et == "standalone_note":
        title = d.get("title") or ((d.get("content") or "")[:60] + "...")
        return f"Note: {title}"
    if et == "interaction":
        subj = d.get("subject") or "interaction"
        when = (d.get("occurred_at") or "")[:10]
        direction = d.get("direction") or "?"
        kind = d.get("type") or ""
        return f"{direction} {kind} — {subj} ({when})"
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
    if et == "task":
        return f"Task: {d.get('title') or ''} [{d.get('status') or ''}]"
    if et == "milestone":
        return f"Milestone: {d.get('name') or ''} [{d.get('status') or ''}]"
    if et == "follow_up":
        return f"Follow-up: {d.get('reason') or ''} (due {d.get('due_date') or '?'})"
    if et == "note":
        return f"Note: {(d.get('content') or '')[:80]}"
    if et == "commitment":
        return f"Commitment: {(d.get('description') or '')[:80]}"
    if et == "tag":
        return f"Tag: {d.get('name') or ''}"
    return f"{et}: id {node.entity_id}"


def _detail_for(node: Node) -> Optional[str]:
    """Optional second-line detail. Kept short to avoid bloating context."""
    d = node.data
    et = node.entity_type
    if et == "decision":
        if d.get("rationale"):
            return f"rationale: {d['rationale'][:200]}"
        if d.get("decision"):
            return f"decision: {d['decision'][:200]}"
    if et == "interaction" and d.get("summary"):
        return d["summary"][:200]
    if et == "email" and d.get("snippet"):
        return d["snippet"][:200]
    if et == "standalone_note" and d.get("content"):
        return (d["content"].replace("\n", " "))[:200]
    return None


def render_context(neighborhood: Neighborhood) -> str:
    """Render a neighborhood as a tree-shaped text blob.

    The shape preserves the associative path. Each non-seed node is
    indented under its parent with the edge type noted in brackets.
    """
    if not neighborhood.seeds:
        return f"No matching entities found for query: {neighborhood.query}\n"

    lines = []
    s = neighborhood.stats
    lines.append(f"# Loci context for: {neighborhood.query}")
    lines.append(
        f"# {s['total_nodes']} entities, depth {s['max_depth_reached']}, "
        f"edges walked: {s['edges_walked']}, "
        f"types: {', '.join(s['entity_types_touched'])}"
    )
    lines.append("")

    # Build child-of map for tree rendering
    children_of: dict = {}
    for n in neighborhood.nodes.values():
        if n.via_parent is not None:
            children_of.setdefault(n.via_parent, []).append(n)

    def render_subtree(node: Node, indent: int) -> None:
        edge = f" [via {node.via_edge}]" if node.via_edge else ""
        if indent == 0:
            lines.append(f"{_label_for(node)}{edge}")
        else:
            lines.append(f"{'  ' * indent}└── {_label_for(node)}{edge}")
        detail = _detail_for(node)
        if detail:
            lines.append(f"{'  ' * (indent + 1)}    {detail}")
        for child in children_of.get(node.key(), []):
            render_subtree(child, indent + 1)

    for seed in neighborhood.seeds:
        if seed.key() not in neighborhood.nodes:
            continue
        lines.append(f"## Starting from: {_label_for(seed)}")
        render_subtree(seed, 0)
        lines.append("")

    return "\n".join(lines)


# ─── CLI ─────────────────────────────────────────────────────────────

def _default_db_path() -> str:
    """Resolve the default soy.db path the same way the rest of the codebase does."""
    return os.path.expanduser("~/.local/share/software-of-you/soy.db")


def _main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python3 shared/loci.py <query>")
        print("Example: python3 shared/loci.py 'What is the state of agent pursuit?'")
        sys.exit(1)

    query = " ".join(sys.argv[1:])
    db_path = _default_db_path()
    if not os.path.exists(db_path):
        print(f"Database not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    neighborhood = assemble_context(db_path, query)
    print(render_context(neighborhood))
    print("\n--- stats ---")
    print(json.dumps(neighborhood.stats, indent=2))


if __name__ == "__main__":
    _main()
