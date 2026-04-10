"""
arms.py — Three context-assembly strategies for the loci benchmark.

Each arm produces a context blob from a prompt. The blob is fed to the
test model alongside the prompt itself. Differences in answer quality
across arms are attributable to differences in context assembly, since
the test model and the prompt are held constant.

Arm A — flat-only:
    Tokenize the prompt, run LIKE search across whitelisted tables,
    return raw rows grouped by entity type. No traversal, no synthesis.
    Closest analog: a single `search` tool call returning flat results.

Arm B — SoY-as-it-is:
    Arm A's flat search PLUS a get_profile-style expansion for any
    contact found in the search results. Mirrors what happens in
    production when the LLM picks the right tool — flat search surfaces
    a contact, then get_profile fills in the contact-centered neighborhood.
    The realistic baseline.

Arm C — loci layer:
    Calls shared.loci.assemble_context with the prompt as the query and
    renders the resulting Neighborhood as a tree-shaped text blob. The
    new thing being tested.

All three arms touch only tables in shared.loci.ALLOWED_TABLES so they
share the same data surface area — the comparison is purely about HOW
the data is assembled, not WHAT data is available.
"""

import json
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from typing import Optional

# Make shared/loci.py importable from this benchmark dir
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from shared.loci import (  # noqa: E402
    ALLOWED_TABLES,
    assemble_context,
    render_context,
)


@dataclass
class ArmResult:
    """One arm's output for one prompt."""
    arm_id: str          # "A", "B", or "C"
    prompt_id: str
    context: str         # the assembled context blob (post-truncation if applied)
    context_chars: int   # length of `context` after any truncation
    metadata: dict       # arm-specific stats (tables touched, rows returned,
                         # plus 'truncated' and 'original_chars' if a char cap was hit)
    elapsed_ms: int
    error: Optional[str] = None


def _apply_char_budget(context: str, metadata: dict, max_chars: Optional[int]) -> str:
    """Truncate context to max_chars if set, recording the truncation in metadata.

    Diego Reyes (panel review) flagged that arm C systematically produces
    larger context blobs than arms A and B, which biases the judge by priors:
    a longer context with more named entities will tend to score higher on
    'completeness' regardless of actual answer quality. The fix is hard
    char-budget parity across arms — every arm lives within the same envelope.

    Truncation is dumb: keep the first N chars, append a clear marker. Smart
    per-arm truncation strategies would introduce their own confound (each
    arm filling its budget differently), so dumb-and-uniform is the right
    primitive even though it cuts arm C mid-node.
    """
    if not max_chars or len(context) <= max_chars:
        metadata.setdefault("truncated", False)
        return context
    original = len(context)
    truncated = context[:max_chars] + (
        f"\n\n[context truncated at {max_chars} chars; "
        f"{original - max_chars} more chars omitted]"
    )
    metadata["truncated"] = True
    metadata["original_chars"] = original
    metadata["truncated_at"] = max_chars
    return truncated


# ─── Local helpers ───────────────────────────────────────────────────
# Small duplicates of shared.loci internals, kept here so arms.py is
# independent of loci's private API. The duplication is intentional:
# changing one shouldn't silently break the other.

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
    """Pull search-useful tokens from a free-text query. Same logic as loci.py."""
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9'-]{1,}", query.lower())
    seen = set()
    out = []
    for t in tokens:
        if t in _STOPWORDS or len(t) < 3 or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _open_db(db_path: str) -> sqlite3.Connection:
    """Open the SoY database with row_factory configured for dict-like access."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _query(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list:
    """Run a parameterized query. Returns list of dicts.

    Logs schema drift to stderr instead of swallowing silently — see the
    matching helper in shared/loci.py for the rationale. A silent fallback
    in a controlled benchmark hides exactly the kind of bug we want to catch.
    """
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except sqlite3.OperationalError as e:
        print(
            f"arms._query: schema drift: {e}\n  query: {sql}\n  params: {params}",
            file=sys.stderr,
        )
        return []


def _parse_id_list(raw) -> list:
    """Parse a linked_contacts/linked_projects field that might be JSON or CSV.
    Mirrors shared.loci._parse_id_list to keep arms.py independent of loci internals."""
    if not raw:
        return []
    if isinstance(raw, list):
        return [int(x) for x in raw if str(x).strip().isdigit()]
    s = str(raw).strip()
    try:
        parsed = json.loads(s)
        if isinstance(parsed, list):
            return [int(x) for x in parsed if str(x).strip().isdigit()]
    except (json.JSONDecodeError, ValueError):
        pass
    return [int(x) for x in re.findall(r"\d+", s)]


# ─── Arm A: flat-only ────────────────────────────────────────────────

# Tables searched and their LIKE patterns. Order matters — this is the
# order they appear in the rendered context. Mirrors what a flat SQL
# search tool would return: six tables, no joins, no synthesis.

_ARM_A_SEARCH_TABLES = [
    ("contact", "contacts",
     "SELECT * FROM contacts WHERE (name LIKE ? OR company LIKE ? OR role LIKE ?) "
     "AND status = 'active' LIMIT ?"),
    ("project", "projects",
     "SELECT * FROM projects WHERE (name LIKE ? OR description LIKE ?) LIMIT ?"),
    ("decision", "decisions",
     "SELECT * FROM decisions WHERE (title LIKE ? OR context LIKE ? OR decision LIKE ?) "
     "ORDER BY decided_at DESC LIMIT ?"),
    ("standalone_note", "standalone_notes",
     "SELECT * FROM standalone_notes WHERE (title LIKE ? OR content LIKE ?) "
     "ORDER BY pinned DESC, created_at DESC LIMIT ?"),
    ("interaction", "contact_interactions",
     "SELECT * FROM contact_interactions WHERE (subject LIKE ? OR summary LIKE ?) "
     "ORDER BY occurred_at DESC LIMIT ?"),
    ("email", "emails",
     "SELECT * FROM emails WHERE (subject LIKE ? OR snippet LIKE ? OR body_preview LIKE ?) "
     "ORDER BY received_at DESC LIMIT ?"),
]

_ARM_A_LIMIT_PER_KEYWORD_PER_TABLE = 3
_ARM_A_MAX_TOTAL_PER_TABLE = 10


def _flat_search(conn: sqlite3.Connection, query: str) -> dict:
    """Run flat LIKE search across whitelisted tables. Returns {entity_type: [rows]}."""
    keywords = _extract_keywords(query) or [query]
    by_type: dict = {}

    for entity_type, _table, sql in _ARM_A_SEARCH_TABLES:
        # Count placeholders excluding the LIMIT placeholder
        placeholder_count = sql.count("?") - 1
        seen_ids = set()
        rows: list = []

        for kw in keywords:
            if len(rows) >= _ARM_A_MAX_TOTAL_PER_TABLE:
                break
            pat = f"%{kw}%"
            params = tuple([pat] * placeholder_count + [_ARM_A_LIMIT_PER_KEYWORD_PER_TABLE])
            for row in _query(conn, sql, params):
                if row["id"] in seen_ids:
                    continue
                seen_ids.add(row["id"])
                rows.append(row)
                if len(rows) >= _ARM_A_MAX_TOTAL_PER_TABLE:
                    break

        if rows:
            by_type[entity_type] = rows

    return by_type


def _render_flat(by_type: dict) -> str:
    """Render flat search results as a list of rows grouped by entity type.
    Intentionally NOT a tree — flat dump is the whole point of arm A."""
    if not by_type:
        return "No matching results.\n"

    sections = []
    for entity_type, rows in by_type.items():
        header = entity_type.upper() + "S"
        lines = [f"## {header} ({len(rows)})"]
        for row in rows:
            if entity_type == "contact":
                role = row.get("role") or "?"
                company = row.get("company") or "?"
                lines.append(f"- {row.get('name')} (id {row['id']}, {role} at {company})")
            elif entity_type == "project":
                bits = [f"{row.get('name')} (id {row['id']}, {row.get('status', '?')}"]
                if row.get("priority"):
                    bits[-1] += f", {row['priority']} priority"
                if row.get("target_date"):
                    bits[-1] += f", target {row['target_date']}"
                bits[-1] += ")"
                lines.append(f"- {bits[0]}")
            elif entity_type == "decision":
                decided = (row.get("decided_at") or "")[:10]
                lines.append(f"- {row.get('title')} (id {row['id']}, decided {decided})")
                if row.get("rationale"):
                    lines.append(f"    rationale: {row['rationale'][:200]}")
            elif entity_type == "standalone_note":
                title = row.get("title") or (row.get("content") or "")[:60]
                lines.append(f"- {title} (id {row['id']})")
                if row.get("content"):
                    preview = row["content"].replace("\n", " ")[:200]
                    lines.append(f"    {preview}")
            elif entity_type == "interaction":
                direction = row.get("direction") or "?"
                kind = row.get("type") or ""
                subj = row.get("subject") or "(no subject)"
                when = (row.get("occurred_at") or "")[:10]
                lines.append(f"- {direction} {kind}: {subj} ({when})")
                if row.get("summary"):
                    lines.append(f"    {row['summary'][:200]}")
            elif entity_type == "email":
                subj = row.get("subject") or "(no subject)"
                when = (row.get("received_at") or "")[:10]
                lines.append(f"- {subj} ({when})")
                if row.get("snippet"):
                    lines.append(f"    {row['snippet'][:200]}")
        sections.append("\n".join(lines))

    return "\n\n".join(sections)


def run_arm_a(db_path: str, prompt: dict, max_chars: Optional[int] = None) -> ArmResult:
    """Arm A: flat-only context assembly."""
    start = time.time()
    error = None
    context = ""
    metadata: dict = {}

    try:
        conn = _open_db(db_path)
        try:
            by_type = _flat_search(conn, prompt["prompt"])
            context = _render_flat(by_type)
            metadata = {
                "tables_searched": [t[1] for t in _ARM_A_SEARCH_TABLES],
                "tables_with_results": list(by_type.keys()),
                "total_rows": sum(len(rows) for rows in by_type.values()),
            }
        finally:
            conn.close()
    except Exception as e:
        error = f"{type(e).__name__}: {e}"

    context = _apply_char_budget(context, metadata, max_chars)
    return ArmResult(
        arm_id="A",
        prompt_id=prompt["id"],
        context=context,
        context_chars=len(context),
        metadata=metadata,
        elapsed_ms=int((time.time() - start) * 1000),
        error=error,
    )


# ─── Arm B: SoY-as-it-is ─────────────────────────────────────────────
# Arm A + a get_profile-style fetch for any contact found in the search.
# Reimplements the contact-scoped queries from profile.py inside the
# harness so this benchmark stays self-contained (no MCP server import).

def _get_profile_for(conn: sqlite3.Connection, cid: int) -> dict:
    """Fetch a contact-centered neighborhood. Mirrors profile.py's contact-scoped queries
    using only ALLOWED_TABLES."""
    profile: dict = {"contact_id": cid}

    rows = _query(conn, "SELECT * FROM contacts WHERE id = ?", (cid,))
    if not rows:
        return profile
    profile["contact"] = rows[0]

    profile["interactions"] = _query(conn,
        "SELECT * FROM contact_interactions WHERE contact_id = ? "
        "ORDER BY occurred_at DESC LIMIT 10",
        (cid,))

    profile["emails"] = _query(conn,
        "SELECT * FROM emails WHERE contact_id = ? ORDER BY received_at DESC LIMIT 10",
        (cid,))

    profile["decisions"] = _query(conn,
        "SELECT * FROM decisions WHERE contact_id = ? ORDER BY decided_at DESC LIMIT 10",
        (cid,))

    profile["projects"] = _query(conn,
        "SELECT * FROM projects WHERE client_id = ? ORDER BY updated_at DESC LIMIT 10",
        (cid,))

    profile["follow_ups"] = _query(conn,
        "SELECT * FROM follow_ups WHERE contact_id = ? AND status = 'pending'",
        (cid,))

    profile["transcripts"] = _query(conn,
        "SELECT t.* FROM transcripts t "
        "JOIN transcript_participants tp ON tp.transcript_id = t.id "
        "WHERE tp.contact_id = ? ORDER BY t.occurred_at DESC LIMIT 10",
        (cid,))

    # calendar_events via contact_ids — defensive parse, no LIKE substring trap.
    # profile.py's calendar branch is currently empty (Sam Okafor caught this in
    # panel review); we add it here for arm B parity with the loci layer.
    event_candidates = _query(conn,
        "SELECT * FROM calendar_events WHERE contact_ids IS NOT NULL "
        "AND contact_ids != '' AND status != 'cancelled' "
        "ORDER BY start_time DESC")
    profile["calendar_events"] = []
    for ev in event_candidates:
        if len(profile["calendar_events"]) >= 10:
            break
        if cid in _parse_id_list(ev.get("contact_ids")):
            profile["calendar_events"].append(ev)

    profile["entity_notes"] = _query(conn,
        "SELECT * FROM notes WHERE entity_type = 'contact' AND entity_id = ? "
        "ORDER BY created_at DESC LIMIT 10",
        (cid,))

    # standalone_notes via linked_contacts. CANNOT use LIKE '%cid%' here:
    # `linked_contacts LIKE '%7%'` matches contact 7 AND contacts 17, 27, 70...
    # any id whose decimal contains the digit 7. Parse the JSON/CSV in Python.
    candidates = _query(conn,
        "SELECT * FROM standalone_notes WHERE linked_contacts IS NOT NULL "
        "AND linked_contacts != '' ORDER BY pinned DESC, created_at DESC")
    profile["standalone_notes"] = []
    for note in candidates:
        if len(profile["standalone_notes"]) >= 10:
            break
        if cid in _parse_id_list(note.get("linked_contacts")):
            profile["standalone_notes"].append(note)

    return profile


def _render_profile(profile: dict) -> str:
    """Render a contact-centered profile as a flat sectioned block.
    Like _render_flat, this is intentionally NOT a tree — arm B is supposed
    to look like 'flat search + a get_profile call,' not a coherent narrative."""
    if "contact" not in profile:
        return ""

    c = profile["contact"]
    lines = [f"\n## Profile: {c.get('name')} (id {c['id']})"]
    if c.get("company"):
        lines.append(f"Company: {c['company']}")
    if c.get("role"):
        lines.append(f"Role: {c['role']}")
    if c.get("email"):
        lines.append(f"Email: {c['email']}")

    if profile.get("projects"):
        lines.append(f"\n### Projects (as client) ({len(profile['projects'])})")
        for p in profile["projects"]:
            lines.append(
                f"- {p.get('name')} [{p.get('status', '?')}, "
                f"{p.get('priority', '?')} priority]"
            )

    if profile.get("interactions"):
        lines.append(f"\n### Interactions ({len(profile['interactions'])})")
        for i in profile["interactions"]:
            direction = i.get("direction") or "?"
            kind = i.get("type") or ""
            subj = i.get("subject") or "(no subject)"
            when = (i.get("occurred_at") or "")[:10]
            lines.append(f"- {direction} {kind}: {subj} ({when})")
            if i.get("summary"):
                lines.append(f"    {i['summary'][:200]}")

    if profile.get("emails"):
        lines.append(f"\n### Emails ({len(profile['emails'])})")
        for e in profile["emails"]:
            subj = e.get("subject") or "(no subject)"
            when = (e.get("received_at") or "")[:10]
            lines.append(f"- {subj} ({when})")
            if e.get("snippet"):
                lines.append(f"    {e['snippet'][:200]}")

    if profile.get("decisions"):
        lines.append(f"\n### Decisions linked to this contact ({len(profile['decisions'])})")
        for d in profile["decisions"]:
            decided = (d.get("decided_at") or "")[:10]
            lines.append(f"- {d.get('title')} (decided {decided})")

    if profile.get("transcripts"):
        lines.append(f"\n### Transcripts ({len(profile['transcripts'])})")
        for t in profile["transcripts"]:
            when = (t.get("occurred_at") or "")[:10]
            lines.append(f"- {t.get('title') or 'Transcript'} ({when})")

    if profile.get("calendar_events"):
        lines.append(f"\n### Calendar events ({len(profile['calendar_events'])})")
        for e in profile["calendar_events"]:
            when = (e.get("start_time") or "")[:16]
            loc = e.get("location")
            location = f" @ {loc}" if loc else ""
            lines.append(f"- {e.get('title') or '(untitled)'} ({when}){location}")

    if profile.get("follow_ups"):
        lines.append(f"\n### Pending follow-ups ({len(profile['follow_ups'])})")
        for f in profile["follow_ups"]:
            lines.append(f"- {f.get('reason')} (due {f.get('due_date')})")

    if profile.get("standalone_notes"):
        lines.append(
            f"\n### Standalone notes mentioning this contact "
            f"({len(profile['standalone_notes'])})"
        )
        for n in profile["standalone_notes"]:
            title = n.get("title") or (n.get("content") or "")[:60]
            lines.append(f"- {title}")

    if profile.get("entity_notes"):
        lines.append(
            f"\n### Notes attached to this contact ({len(profile['entity_notes'])})"
        )
        for n in profile["entity_notes"]:
            lines.append(f"- {(n.get('content') or '')[:80]}")

    return "\n".join(lines)


def run_arm_b(db_path: str, prompt: dict, max_chars: Optional[int] = None) -> ArmResult:
    """Arm B: flat search PLUS get_profile-style expansion for any contact found."""
    start = time.time()
    error = None
    context = ""
    metadata: dict = {}

    try:
        conn = _open_db(db_path)
        try:
            by_type = _flat_search(conn, prompt["prompt"])
            flat = _render_flat(by_type)

            profile_blocks = []
            profiled_contacts = []
            for contact_row in by_type.get("contact", []):
                cid = contact_row["id"]
                profile = _get_profile_for(conn, cid)
                rendered = _render_profile(profile)
                if rendered:
                    profile_blocks.append(rendered)
                    profiled_contacts.append(cid)

            context = flat
            if profile_blocks:
                context += "\n\n# Expanded contact profiles\n" + "\n".join(profile_blocks)

            metadata = {
                "tables_with_results": list(by_type.keys()),
                "total_flat_rows": sum(len(rows) for rows in by_type.values()),
                "profiles_expanded": len(profiled_contacts),
                "profiled_contact_ids": profiled_contacts,
            }
        finally:
            conn.close()
    except Exception as e:
        error = f"{type(e).__name__}: {e}"

    context = _apply_char_budget(context, metadata, max_chars)
    return ArmResult(
        arm_id="B",
        prompt_id=prompt["id"],
        context=context,
        context_chars=len(context),
        metadata=metadata,
        elapsed_ms=int((time.time() - start) * 1000),
        error=error,
    )


# ─── Arm C: loci layer ───────────────────────────────────────────────

def run_arm_c(db_path: str, prompt: dict, max_chars: Optional[int] = None) -> ArmResult:
    """Arm C: graph traversal via shared.loci.assemble_context."""
    start = time.time()
    error = None
    context = ""
    metadata: dict = {}

    try:
        neighborhood = assemble_context(db_path, prompt["prompt"])
        context = render_context(neighborhood)
        metadata = dict(neighborhood.stats)
    except Exception as e:
        error = f"{type(e).__name__}: {e}"

    context = _apply_char_budget(context, metadata, max_chars)
    return ArmResult(
        arm_id="C",
        prompt_id=prompt["id"],
        context=context,
        context_chars=len(context),
        metadata=metadata,
        elapsed_ms=int((time.time() - start) * 1000),
        error=error,
    )


# ─── Dispatch ─────────────────────────────────────────────────────────

ARMS = {
    "A": ("flat-only", run_arm_a),
    "B": ("SoY-as-it-is", run_arm_b),
    "C": ("loci layer", run_arm_c),
}


def run_arm(arm_id: str, db_path: str, prompt: dict,
            max_chars: Optional[int] = None) -> ArmResult:
    """Run a single arm against a single prompt with an optional context char cap."""
    if arm_id not in ARMS:
        raise ValueError(f"Unknown arm: {arm_id}. Valid: {list(ARMS.keys())}")
    _, func = ARMS[arm_id]
    return func(db_path, prompt, max_chars=max_chars)
