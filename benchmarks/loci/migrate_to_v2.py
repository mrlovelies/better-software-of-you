#!/usr/bin/env python3
"""Production migration: add V2 tables + backfill to real soy.db.

Applies the next_soy V2 schema additions IN PLACE to the live soy.db.
Unlike seed_next_soy.py (which creates a parallel DB), this modifies the
production database. All operations are idempotent — safe to re-run.

What it does:
  1. Recreate contacts table with expanded status enum + merge columns
  2. Add 8 new tables (entity_edges, contact_identities, notes_v2, etc.)
  3. Translate standalone_notes → notes_v2
  4. Backfill entity_edges from structural FKs
  5. Parse legacy linked_* columns into mentions edges
  6. Backfill contact_identities from contacts.email
  7. Resolve James Andrews duplicate
  8. Apply audit-driven status flips + Kerry promotion + new contacts
  9. Seed memory episodes
  10. Populate wikilinks
  11. Validate + report

What it does NOT do:
  - Rename 'tasks' to 'project_tasks' (production code depends on 'tasks')
  - Drop standalone_notes (kept for backwards compat, notes_v2 is additive)
  - Gmail ingest (separate step)

Usage:
    python3 benchmarks/loci/migrate_to_v2.py          # dry-run by default
    python3 benchmarks/loci/migrate_to_v2.py --apply   # actually modify soy.db
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

SOY_DB = Path.home() / ".local" / "share" / "software-of-you" / "soy.db"
SCRIPT_DIR = Path(__file__).resolve().parent

# Import seed data constants from the seed script
sys.path.insert(0, str(SCRIPT_DIR))
from seed_next_soy import (
    NEW_CONTACTS,
    NEW_EDGES,
    ADDITIONAL_IDENTITIES,
    STATUS_FLIPS,
    KERRY_PROMOTION,
    SEED_EPISODES,
    HAND_CURATED_WIKILINKS,
    STRUCTURAL_EDGES,
    _parse_id_list,
    _name_in_linked_field,
    _raw_linked_strings,
    _now_iso,
)


def _log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Step 1 — Recreate contacts table with expanded status + merge columns
# ---------------------------------------------------------------------------

def migrate_contacts_table(conn, dry_run):
    """Expand contacts.status CHECK constraint and add merge tracking columns.

    SQLite doesn't support ALTER TABLE ... ALTER COLUMN, so we recreate
    the table. Steps: create temp, copy data, drop old, rename temp,
    recreate indexes. All in one transaction.
    """
    # Check if migration already applied
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='contacts'"
    ).fetchone()
    ddl = row[0] if row else ""

    if "'prospect'" in ddl and "merged_into_id" in ddl:
        _log("contacts table already migrated (expanded status + merge columns)")
        return False

    _log("Step 1: recreating contacts table with expanded status + merge columns")
    if dry_run:
        _log("  [DRY RUN] would recreate contacts table")
        return False

    # Get existing indexes and views to recreate after
    indexes = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='contacts' "
        "AND sql IS NOT NULL"
    ).fetchall()

    # Save and drop ALL views (cascading deps make selective dropping fragile)
    views = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='view' AND sql IS NOT NULL"
    ).fetchall()
    for v in views:
        conn.execute(f"DROP VIEW IF EXISTS [{v['name']}]")

    conn.execute("""
        CREATE TABLE contacts_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT,
            phone TEXT,
            company TEXT,
            role TEXT,
            type TEXT NOT NULL DEFAULT 'individual'
                CHECK (type IN ('individual', 'company')),
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'prospect', 'inactive', 'broadcast_only', 'archived')),
            notes TEXT,
            merged_into_id INTEGER REFERENCES contacts_v2(id),
            merged_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    conn.execute("""
        INSERT INTO contacts_v2
            (id, name, email, phone, company, role, type, status, notes,
             created_at, updated_at)
        SELECT id, name, email, phone, company, role, type, status, notes,
               created_at, updated_at
        FROM contacts
    """)

    conn.execute("DROP TABLE contacts")
    conn.execute("ALTER TABLE contacts_v2 RENAME TO contacts")

    # Recreate indexes
    for idx in indexes:
        try:
            conn.execute(idx[0])
        except sqlite3.OperationalError:
            pass  # index may already exist

    # Recreate views
    for v in views:
        try:
            conn.execute(v["sql"])
        except sqlite3.OperationalError as e:
            _log(f"  WARN: could not recreate view {v['name']}: {e}")

    conn.commit()
    _log("  contacts table recreated with expanded status enum + merge columns")
    return True


# ---------------------------------------------------------------------------
# Step 2 — Add new tables (idempotent CREATE IF NOT EXISTS)
# ---------------------------------------------------------------------------

NEW_TABLES_DDL = """
CREATE TABLE IF NOT EXISTS contact_identities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    identity_type TEXT NOT NULL
        CHECK (identity_type IN ('email','phone','linkedin','github_handle','discord_handle','alias_name','external_id')),
    identity_value TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    first_seen TEXT,
    last_seen TEXT,
    verified INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'manual'
        CHECK (source IN ('manual','backfill','import','merge')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (identity_type, identity_value)
);
CREATE INDEX IF NOT EXISTS idx_identities_canonical ON contact_identities(canonical_contact_id);
CREATE INDEX IF NOT EXISTS idx_identities_value ON contact_identities(identity_type, identity_value);

CREATE TABLE IF NOT EXISTS notes_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    content TEXT NOT NULL,
    note_kind TEXT NOT NULL DEFAULT 'freeform'
        CHECK (note_kind IN ('freeform','meeting','idea','decision_draft','brief','observation','reference')),
    pinned INTEGER NOT NULL DEFAULT 0,
    promoted_to_type TEXT,
    promoted_to_id INTEGER,
    source TEXT NOT NULL DEFAULT 'manual'
        CHECK (source IN ('manual','daily_log_extract','email_clip','voice_memo','import','migrated_from_standalone')),
    source_ref TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_notes_v2_kind ON notes_v2(note_kind);
CREATE INDEX IF NOT EXISTS idx_notes_v2_pinned ON notes_v2(pinned) WHERE pinned = 1;
CREATE INDEX IF NOT EXISTS idx_notes_v2_created ON notes_v2(created_at);

CREATE TABLE IF NOT EXISTS daily_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    log_date TEXT NOT NULL UNIQUE,
    content TEXT NOT NULL,
    mood TEXT,
    energy INTEGER CHECK (energy BETWEEN 1 AND 5),
    focus_area TEXT,
    auto_summary TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_daily_logs_date ON daily_logs(log_date);

CREATE TABLE IF NOT EXISTS log_mentions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    log_id INTEGER NOT NULL REFERENCES daily_logs(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    mention_text TEXT,
    char_start INTEGER,
    char_end INTEGER,
    confidence REAL NOT NULL DEFAULT 1.0,
    mention_status TEXT NOT NULL DEFAULT 'resolved'
        CHECK (mention_status IN ('resolved','suggested','rejected')),
    resolution_source TEXT NOT NULL
        CHECK (resolution_source IN ('wikilink','name_match','user_confirmed','llm_suggested')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_log_mentions_log ON log_mentions(log_id);
CREATE INDEX IF NOT EXISTS idx_log_mentions_entity ON log_mentions(entity_type, entity_id);

CREATE TABLE IF NOT EXISTS wikilinks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alias TEXT NOT NULL,
    canonical_type TEXT NOT NULL,
    canonical_id INTEGER NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 1.0,
    created_by TEXT NOT NULL DEFAULT 'user'
        CHECK (created_by IN ('user','auto_name_match','import')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (alias, canonical_type, canonical_id)
);
CREATE INDEX IF NOT EXISTS idx_wikilinks_alias ON wikilinks(alias);
CREATE INDEX IF NOT EXISTS idx_wikilinks_entity ON wikilinks(canonical_type, canonical_id);

CREATE TABLE IF NOT EXISTS memory_episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    summary TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    episode_type TEXT
        CHECK (episode_type IN ('project_phase','relationship_phase','life_event','conceptual_thread','user_defined')),
    emotional_tone TEXT,
    created_by TEXT NOT NULL DEFAULT 'user'
        CHECK (created_by IN ('user','auto_cluster','import')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_episodes_active ON memory_episodes(started_at) WHERE ended_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_episodes_type ON memory_episodes(episode_type);

CREATE TABLE IF NOT EXISTS episode_members (
    episode_id INTEGER NOT NULL REFERENCES memory_episodes(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    role TEXT,
    added_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (episode_id, entity_type, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_episode_members_entity ON episode_members(entity_type, entity_id);

CREATE TABLE IF NOT EXISTS entity_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    src_type TEXT NOT NULL,
    src_id INTEGER NOT NULL,
    dst_type TEXT NOT NULL,
    dst_id INTEGER NOT NULL,
    edge_type TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0
        CHECK (weight >= 0.0 AND weight <= 1.0),
    established_at TEXT,
    ended_at TEXT,
    source TEXT NOT NULL DEFAULT 'manual'
        CHECK (source IN ('manual','backfill','wikilink','import','merge','user_pin')),
    metadata TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (src_type, src_id, dst_type, dst_id, edge_type)
);
CREATE INDEX IF NOT EXISTS idx_edges_src ON entity_edges(src_type, src_id, edge_type);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON entity_edges(dst_type, dst_id, edge_type);
CREATE INDEX IF NOT EXISTS idx_edges_type ON entity_edges(edge_type);
CREATE INDEX IF NOT EXISTS idx_edges_active ON entity_edges(src_type, src_id) WHERE ended_at IS NULL;
"""


def add_new_tables(conn, dry_run):
    _log("Step 2: adding 8 new tables (IF NOT EXISTS)")
    if dry_run:
        _log("  [DRY RUN] would create tables")
        return
    conn.executescript(NEW_TABLES_DDL)
    conn.commit()
    _log("  done")


# ---------------------------------------------------------------------------
# Step 3 — Translate standalone_notes → notes_v2
# ---------------------------------------------------------------------------

def _table_exists(conn, name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def translate_standalone_notes(conn, dry_run):
    if _table_exists(conn, "notes_v2"):
        existing = conn.execute("SELECT COUNT(*) FROM notes_v2").fetchone()[0]
        if existing > 0:
            _log(f"Step 3: notes_v2 already has {existing} rows, skipping translation")
            return

    sn_count = conn.execute("SELECT COUNT(*) FROM standalone_notes").fetchone()[0]
    _log(f"Step 3: translating {sn_count} standalone_notes → notes_v2")
    if dry_run:
        _log("  [DRY RUN] would translate")
        return

    conn.execute("""
        INSERT INTO notes_v2 (id, title, content, note_kind, pinned,
                              source, created_at, updated_at)
        SELECT id, title, content, 'freeform', pinned,
               'migrated_from_standalone', created_at, updated_at
        FROM standalone_notes
    """)
    conn.commit()
    _log(f"  translated {sn_count} rows")


# ---------------------------------------------------------------------------
# Step 4 — Structural edge backfill
# ---------------------------------------------------------------------------

def backfill_structural_edges(conn, dry_run):
    if not _table_exists(conn, "entity_edges"):
        if dry_run:
            _log("Step 4: [DRY RUN] entity_edges doesn't exist yet")
            return
    existing = conn.execute("SELECT COUNT(*) FROM entity_edges WHERE source='backfill'").fetchone()[0]
    if existing > 0:
        _log(f"Step 4: entity_edges already has {existing} backfill rows, skipping")
        return

    _log("Step 4: backfilling entity_edges from structural FKs")
    if dry_run:
        _log("  [DRY RUN] would backfill")
        return

    total = 0
    # Note: in production, tasks table is still called 'tasks' (not project_tasks)
    prod_edges = []
    for spec in STRUCTURAL_EDGES:
        s = dict(spec)
        if s['table'] == 'project_tasks':
            s['table'] = 'tasks'
            s['src_type'] = 'task'  # keep production entity type
        prod_edges.append(s)

    for spec in prod_edges:
        sql = (
            f"INSERT OR IGNORE INTO entity_edges "
            f"(src_type, src_id, dst_type, dst_id, edge_type, source, metadata) "
            f"SELECT '{spec['src_type']}', {spec['src_col']}, "
            f"'{spec['dst_type']}', {spec['dst_col']}, "
            f"'{spec['edge_type']}', 'backfill', "
            f"json_object('original_column', '{spec['origin_col']}') "
            f"FROM {spec['table']} WHERE {spec['where']}"
        )
        cur = conn.execute(sql)
        n = cur.rowcount if cur.rowcount != -1 else 0
        total += n

    # event_with from calendar_events.contact_ids (TEXT list parse)
    for row in conn.execute(
        "SELECT id, contact_ids FROM calendar_events WHERE contact_ids IS NOT NULL"
    ):
        for cid in _parse_id_list(row["contact_ids"]):
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO entity_edges "
                    "(src_type, src_id, dst_type, dst_id, edge_type, source, metadata) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    ("calendar_event", row["id"], "contact", cid, "event_with", "backfill",
                     json.dumps({"original_column": "calendar_events.contact_ids"})),
                )
                total += 1
            except sqlite3.IntegrityError:
                pass

    conn.commit()
    _log(f"  backfilled {total} structural edges")


# ---------------------------------------------------------------------------
# Step 5 — Parse legacy linked_* → mentions edges
# ---------------------------------------------------------------------------

def parse_legacy_links(conn, dry_run):
    if not _table_exists(conn, "entity_edges"):
        if dry_run:
            _log("Step 5: [DRY RUN] entity_edges doesn't exist yet")
            return
    existing = conn.execute(
        "SELECT COUNT(*) FROM entity_edges WHERE edge_type='mentions' AND source='backfill'"
    ).fetchone()[0]
    if existing > 0:
        _log(f"Step 5: already have {existing} mentions edges, skipping")
        return

    _log("Step 5: parsing legacy linked_* columns into mentions edges")
    if dry_run:
        _log("  [DRY RUN] would parse")
        return

    project_names = {r["id"]: r["name"]
                     for r in conn.execute("SELECT id, name FROM projects")}
    contact_names = {r["id"]: r["name"]
                     for r in conn.execute("SELECT id, name FROM contacts")}
    project_name_to_id = {v: k for k, v in project_names.items()}
    contact_name_to_id = {v: k for k, v in contact_names.items()}

    count = 0
    for row in conn.execute(
        "SELECT id, linked_contacts, linked_projects FROM standalone_notes"
    ):
        nid = row["id"]
        for cid in _parse_id_list(row["linked_contacts"]):
            if cid in contact_names:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO entity_edges "
                        "(src_type, src_id, dst_type, dst_id, edge_type, source, metadata) "
                        "VALUES ('notes_v2', ?, 'contact', ?, 'mentions', 'backfill', ?)",
                        (nid, cid, json.dumps({"origin": "standalone_notes.linked_contacts"})),
                    )
                    count += 1
                except sqlite3.IntegrityError:
                    pass
        for name in _raw_linked_strings(row["linked_contacts"]):
            cid = contact_name_to_id.get(name)
            if cid:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO entity_edges "
                        "(src_type, src_id, dst_type, dst_id, edge_type, source, metadata) "
                        "VALUES ('notes_v2', ?, 'contact', ?, 'mentions', 'backfill', ?)",
                        (nid, cid, json.dumps({"origin": "standalone_notes.linked_contacts (name)"})),
                    )
                    count += 1
                except sqlite3.IntegrityError:
                    pass
        for pid in _parse_id_list(row["linked_projects"]):
            if pid in project_names:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO entity_edges "
                        "(src_type, src_id, dst_type, dst_id, edge_type, source, metadata) "
                        "VALUES ('notes_v2', ?, 'project', ?, 'mentions', 'backfill', ?)",
                        (nid, pid, json.dumps({"origin": "standalone_notes.linked_projects"})),
                    )
                    count += 1
                except sqlite3.IntegrityError:
                    pass
        for name in _raw_linked_strings(row["linked_projects"]):
            pid = project_name_to_id.get(name)
            if pid:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO entity_edges "
                        "(src_type, src_id, dst_type, dst_id, edge_type, source, metadata) "
                        "VALUES ('notes_v2', ?, 'project', ?, 'mentions', 'backfill', ?)",
                        (nid, pid, json.dumps({"origin": "standalone_notes.linked_projects (name)"})),
                    )
                    count += 1
                except sqlite3.IntegrityError:
                    pass

    conn.commit()
    _log(f"  parsed {count} mentions edges")


# ---------------------------------------------------------------------------
# Step 6 — Contact identities
# ---------------------------------------------------------------------------

def backfill_contact_identities(conn, dry_run):
    if not _table_exists(conn, "contact_identities"):
        if dry_run:
            _log("Step 6: [DRY RUN] contact_identities doesn't exist yet")
            return
    existing = conn.execute("SELECT COUNT(*) FROM contact_identities").fetchone()[0]
    if existing > 0:
        _log(f"Step 6: contact_identities already has {existing} rows, skipping")
        return

    _log("Step 6: backfilling contact_identities from contacts.email")
    if dry_run:
        _log("  [DRY RUN] would backfill")
        return

    conn.execute("""
        INSERT OR IGNORE INTO contact_identities
            (canonical_contact_id, identity_type, identity_value,
             confidence, verified, source)
        SELECT id, 'email', email, 1.0, 1, 'backfill'
        FROM contacts WHERE email IS NOT NULL AND email != ''
    """)
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM contact_identities").fetchone()[0]
    _log(f"  backfilled {n} identity rows")


# ---------------------------------------------------------------------------
# Step 7 — James Andrews merge
# ---------------------------------------------------------------------------

def resolve_duplicate(conn, dry_run):
    try:
        row9 = conn.execute("SELECT id, name, merged_into_id FROM contacts WHERE id = 9").fetchone()
    except sqlite3.OperationalError:
        # merged_into_id column doesn't exist yet (dry-run before contacts table recreated)
        if dry_run:
            _log("Step 7: [DRY RUN] would merge James Andrews duplicate")
            return
        row9 = None
    if not row9:
        _log("Step 7: contact id 9 not found, skipping merge")
        return
    if row9["merged_into_id"]:
        _log("Step 7: contact id 9 already merged, skipping")
        return
    if "James Andrews" not in row9["name"]:
        _log(f"Step 7: contact id 9 is {row9['name']!r}, not James Andrews, skipping")
        return

    _log("Step 7: merging James Andrews duplicate (id 9 → id 7)")
    if dry_run:
        _log("  [DRY RUN] would merge")
        return

    conn.execute("UPDATE contacts SET merged_into_id = 7, merged_at = datetime('now'), "
                 "status = 'inactive' WHERE id = 9")
    conn.execute("UPDATE entity_edges SET dst_id = 7 WHERE dst_type = 'contact' AND dst_id = 9")
    conn.execute("UPDATE entity_edges SET src_id = 7 WHERE src_type = 'contact' AND src_id = 9")
    conn.execute("DELETE FROM contact_identities WHERE canonical_contact_id = 9")
    conn.commit()
    _log("  merged")


# ---------------------------------------------------------------------------
# Step 8 — Audit-driven additions (status flips, Kerry, new contacts, edges)
# ---------------------------------------------------------------------------

def apply_audit_additions(conn, dry_run):
    # Check if already applied by looking for Alex contact
    alex = conn.execute(
        "SELECT id FROM contacts WHERE name = 'Alex Somerville'"
    ).fetchone()
    if alex:
        _log(f"Step 8: audit additions already applied (Alex id={alex['id']})")
        return alex["id"], _load_key_to_id(conn)

    _log("Step 8: applying audit-driven additions")
    if dry_run:
        _log("  [DRY RUN] would apply status flips + Kerry promotion + new contacts + edges")
        return None, {}

    # Status flips
    for cid, new_status, _reason in STATUS_FLIPS:
        conn.execute("UPDATE contacts SET status = ? WHERE id = ?", (new_status, cid))

    # Kerry promotion
    conn.execute(
        "UPDATE contacts SET email = ?, company = ?, role = ?, status = ? WHERE id = ?",
        (KERRY_PROMOTION["email"], KERRY_PROMOTION["company"],
         KERRY_PROMOTION["role"], KERRY_PROMOTION["status"], KERRY_PROMOTION["id"]),
    )
    conn.execute(
        "INSERT OR IGNORE INTO contact_identities "
        "(canonical_contact_id, identity_type, identity_value, confidence, verified, source) "
        "VALUES (?, 'email', ?, 1.0, 1, 'merge')",
        (KERRY_PROMOTION["id"], KERRY_PROMOTION["email"]),
    )

    # New contacts
    key_to_id = {}
    for c in NEW_CONTACTS:
        cur = conn.execute(
            "INSERT INTO contacts (name, email, phone, company, role, type, status, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (c["name"], c["email"], c["phone"], c["company"],
             c["role"], c["type"], c["status"], c["notes"]),
        )
        key_to_id[c["key"]] = cur.lastrowid
        if c["email"]:
            conn.execute(
                "INSERT OR IGNORE INTO contact_identities "
                "(canonical_contact_id, identity_type, identity_value, confidence, verified, source) "
                "VALUES (?, 'email', ?, 1.0, 1, 'import')",
                (cur.lastrowid, c["email"]),
            )

    # user_contact_id in soy_meta
    conn.execute(
        "INSERT OR REPLACE INTO soy_meta (key, value) VALUES ('user_contact_id', ?)",
        (str(key_to_id["alex"]),),
    )

    # Additional identities
    for handle, itype, value, confidence, verified in ADDITIONAL_IDENTITIES:
        target_id = KERRY_PROMOTION["id"] if handle == "kerry_existing" else key_to_id.get(handle)
        if target_id:
            conn.execute(
                "INSERT OR IGNORE INTO contact_identities "
                "(canonical_contact_id, identity_type, identity_value, confidence, verified, source) "
                "VALUES (?, ?, ?, ?, ?, 'import')",
                (target_id, itype, value, confidence, verified),
            )

    # New edges
    def resolve(handle):
        if handle.startswith("existing:"):
            return "contact", int(handle.split(":", 1)[1])
        if handle.startswith("elana_existing:"):
            return "contact", int(handle.split(":", 1)[1])
        if handle.startswith("project:"):
            return "project", int(handle.split(":", 1)[1])
        if handle in key_to_id:
            return "contact", key_to_id[handle]
        raise KeyError(f"cannot resolve: {handle!r}")

    for src, dst, edge_type, metadata in NEW_EDGES:
        src_type, src_id = resolve(src)
        dst_type, dst_id = resolve(dst)
        try:
            conn.execute(
                "INSERT INTO entity_edges "
                "(src_type, src_id, dst_type, dst_id, edge_type, source, metadata) "
                "VALUES (?, ?, ?, ?, ?, 'manual', ?)",
                (src_type, src_id, dst_type, dst_id, edge_type,
                 json.dumps(metadata) if metadata else None),
            )
        except sqlite3.IntegrityError:
            pass

    conn.commit()
    _log(f"  applied: {len(STATUS_FLIPS)} status flips, Kerry promoted, "
         f"{len(key_to_id)} new contacts, edges materialized")
    return key_to_id.get("alex"), key_to_id


def _load_key_to_id(conn):
    """Reload key→id map for contacts that were already inserted."""
    key_to_id = {}
    for c in NEW_CONTACTS:
        row = conn.execute(
            "SELECT id FROM contacts WHERE name = ?", (c["name"],)
        ).fetchone()
        if row:
            key_to_id[c["key"]] = row["id"]
    return key_to_id


# ---------------------------------------------------------------------------
# Step 9 — Memory episodes
# ---------------------------------------------------------------------------

def seed_episodes(conn, dry_run, key_to_id):
    if not _table_exists(conn, "memory_episodes"):
        if dry_run:
            _log("Step 9: [DRY RUN] memory_episodes doesn't exist yet")
            return
    existing = conn.execute("SELECT COUNT(*) FROM memory_episodes").fetchone()[0]
    if existing > 0:
        _log(f"Step 9: memory_episodes already has {existing} rows, skipping")
        return

    _log("Step 9: seeding 4 memory episodes")
    if dry_run:
        _log("  [DRY RUN] would seed episodes")
        return

    ENTITY_TABLES = {
        "contact": ("contacts", "id"),
        "project": ("projects", "id"),
        "notes_v2": ("notes_v2", "id"),
    }

    for ep in SEED_EPISODES:
        cur = conn.execute(
            "INSERT INTO memory_episodes "
            "(title, summary, started_at, ended_at, episode_type, emotional_tone, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, 'import')",
            (ep["title"], ep["summary"], ep["started_at"], ep["ended_at"],
             ep["episode_type"], ep["emotional_tone"]),
        )
        episode_id = cur.lastrowid

        for entity_type, resolver_kind, resolver_value, role in ep["members"]:
            entity_id = None
            if resolver_kind == "id":
                entity_id = resolver_value
            elif resolver_kind == "key":
                entity_id = key_to_id.get(resolver_value)
                if entity_id is None:
                    continue
            elif resolver_kind == "title_like":
                row = conn.execute(
                    "SELECT id FROM notes_v2 WHERE title LIKE ? LIMIT 1",
                    (resolver_value,),
                ).fetchone()
                if row:
                    entity_id = row["id"]
                else:
                    continue

            if entity_id is None:
                continue

            tbl, pk = ENTITY_TABLES.get(entity_type, (None, None))
            if tbl:
                exists = conn.execute(
                    f"SELECT 1 FROM {tbl} WHERE {pk} = ?", (entity_id,)
                ).fetchone()
                if not exists:
                    continue

            try:
                conn.execute(
                    "INSERT INTO episode_members (episode_id, entity_type, entity_id, role) "
                    "VALUES (?, ?, ?, ?)",
                    (episode_id, entity_type, entity_id, role),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO entity_edges "
                    "(src_type, src_id, dst_type, dst_id, edge_type, source, metadata) "
                    "VALUES (?, ?, 'memory_episode', ?, 'part_of_episode', 'manual', ?)",
                    (entity_type, entity_id, episode_id,
                     json.dumps({"role": role, "episode_title": ep["title"]})),
                )
            except sqlite3.IntegrityError:
                pass

    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM memory_episodes").fetchone()[0]
    m = conn.execute("SELECT COUNT(*) FROM episode_members").fetchone()[0]
    _log(f"  seeded {n} episodes with {m} members")


# ---------------------------------------------------------------------------
# Step 10 — Wikilinks
# ---------------------------------------------------------------------------

def populate_wikilinks(conn, dry_run, key_to_id):
    if not _table_exists(conn, "wikilinks"):
        if dry_run:
            _log("Step 10: [DRY RUN] wikilinks doesn't exist yet")
            return
    existing = conn.execute("SELECT COUNT(*) FROM wikilinks").fetchone()[0]
    if existing > 0:
        _log(f"Step 10: wikilinks already has {existing} rows, skipping")
        return

    _log("Step 10: populating wikilinks")
    if dry_run:
        _log("  [DRY RUN] would populate")
        return

    # Primary aliases
    for row in conn.execute("SELECT id, name FROM contacts WHERE merged_into_id IS NULL"):
        conn.execute(
            "INSERT OR IGNORE INTO wikilinks (alias, canonical_type, canonical_id, is_primary, confidence, created_by) "
            "VALUES (?, 'contact', ?, 1, 1.0, 'import')", (row["name"], row["id"]),
        )
    for row in conn.execute("SELECT id, name FROM projects"):
        conn.execute(
            "INSERT OR IGNORE INTO wikilinks (alias, canonical_type, canonical_id, is_primary, confidence, created_by) "
            "VALUES (?, 'project', ?, 1, 1.0, 'import')", (row["name"], row["id"]),
        )
    for row in conn.execute("SELECT id, title FROM decisions"):
        conn.execute(
            "INSERT OR IGNORE INTO wikilinks (alias, canonical_type, canonical_id, is_primary, confidence, created_by) "
            "VALUES (?, 'decision', ?, 1, 1.0, 'import')", (row["title"], row["id"]),
        )

    # Hand-curated shortforms
    for alias, kind, value in HAND_CURATED_WIKILINKS:
        if kind == "ambiguous":
            continue
        if kind == "contact_id":
            ctype, cid = "contact", int(value)
        elif kind == "project_id":
            ctype, cid = "project", int(value)
        elif kind == "contact_key":
            cid = key_to_id.get(value)
            if cid is None:
                continue
            ctype = "contact"
        else:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO wikilinks (alias, canonical_type, canonical_id, is_primary, confidence, created_by) "
            "VALUES (?, ?, ?, 0, 1.0, 'user')", (alias, ctype, cid),
        )

    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM wikilinks").fetchone()[0]
    _log(f"  populated {n} wikilinks")


# ---------------------------------------------------------------------------
# Step 11 — Schema version marker
# ---------------------------------------------------------------------------

def mark_schema_version(conn, dry_run):
    if dry_run:
        return
    conn.execute(
        "INSERT OR REPLACE INTO soy_meta (key, value) VALUES ('next_soy_schema_version', '001_core')"
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate(conn):
    _log("Validating...")
    checks = []

    # entity_edges has rows
    n = conn.execute("SELECT COUNT(*) FROM entity_edges").fetchone()[0]
    checks.append(("entity_edges has rows", n > 0, f"{n} edges"))

    # Contact status distribution
    rows = conn.execute("SELECT status, COUNT(*) c FROM contacts GROUP BY status").fetchall()
    dist = {r["status"]: r["c"] for r in rows}
    required = ("active", "prospect", "broadcast_only")
    missing = [s for s in required if dist.get(s, 0) == 0]
    checks.append(("Status distribution", not missing, str(dist)))

    # building_site_for edges exist
    n = conn.execute(
        "SELECT COUNT(*) FROM entity_edges WHERE edge_type = 'building_site_for'"
    ).fetchone()[0]
    checks.append(("building_site_for edges", n >= 4, f"{n} edges"))

    # Kerry check
    kerry = conn.execute("SELECT email, status FROM contacts WHERE id = 6").fetchone()
    kerry_ok = kerry and kerry["email"] and kerry["status"] == "active"
    checks.append(("Kerry promoted", kerry_ok,
                    f"email={kerry['email'] if kerry else None}"))

    # notes_v2 populated
    n = conn.execute("SELECT COUNT(*) FROM notes_v2").fetchone()[0]
    checks.append(("notes_v2 populated", n > 0, f"{n} rows"))

    # episodes exist
    n = conn.execute("SELECT COUNT(*) FROM memory_episodes").fetchone()[0]
    checks.append(("Episodes seeded", n >= 4, f"{n} episodes"))

    passed = sum(1 for _, ok, _ in checks if ok)
    _log(f"Validation: {passed}/{len(checks)} passed")
    for name, ok, detail in checks:
        mark = "✓" if ok else "✗"
        _log(f"  {mark} {name}: {detail}")

    return all(ok for _, ok, _ in checks)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Production migration: add V2 tables to soy.db")
    parser.add_argument("--apply", action="store_true",
                        help="Actually modify soy.db (default is dry-run)")
    args = parser.parse_args()
    dry_run = not args.apply

    if dry_run:
        _log("=== DRY RUN (use --apply to modify soy.db) ===")
    else:
        _log("=== APPLYING MIGRATION TO PRODUCTION soy.db ===")

    if not SOY_DB.exists():
        sys.exit(f"FATAL: {SOY_DB} not found")

    conn = sqlite3.connect(SOY_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")

    try:
        migrate_contacts_table(conn, dry_run)
        add_new_tables(conn, dry_run)
        translate_standalone_notes(conn, dry_run)
        backfill_structural_edges(conn, dry_run)
        parse_legacy_links(conn, dry_run)
        backfill_contact_identities(conn, dry_run)
        resolve_duplicate(conn, dry_run)
        alex_id, key_to_id = apply_audit_additions(conn, dry_run)
        seed_episodes(conn, dry_run, key_to_id)
        populate_wikilinks(conn, dry_run, key_to_id)
        mark_schema_version(conn, dry_run)

        if not dry_run:
            if validate(conn):
                _log("Migration complete. All checks passed.")
            else:
                _log("Migration complete with validation failures. Review above.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
