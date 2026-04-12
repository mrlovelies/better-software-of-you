-- next_soy_schema 001_core.sql
-- v1 DDL for the parallel benchmark database, per next_soy_schema_v1.md (locked 2026-04-11).
--
-- Structure:
--   1. Carry-over tables (cloned from real soy.db schema at the time of writing,
--      with minor adjustments: contacts gains merged_into_id/merged_at and an
--      expanded status enum; tasks is renamed to project_tasks; FKs to tables
--      we don't carry over are dropped rather than dangling).
--   2. New tables per the DDL doc: contact_identities, notes_v2, daily_logs,
--      log_mentions, wikilinks, memory_episodes, episode_members, entity_edges.
--   3. Indexes and a single meta marker so loci_v2 can assert the schema version.
--
-- Foreign-key enforcement is OFF at seed time (the seed script attaches real
-- SoY in read-only mode and copies rows; FK violations from partial inserts
-- would mask bugs, so we rely on validate() instead of per-row enforcement).

PRAGMA foreign_keys = OFF;

-- ---------------------------------------------------------------------------
-- 1. Carry-over tables
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS soy_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    company TEXT,
    role TEXT,
    type TEXT NOT NULL DEFAULT 'individual'
        CHECK (type IN ('individual', 'company')),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'prospect', 'inactive', 'broadcast_only')),
    notes TEXT,
    merged_into_id INTEGER REFERENCES contacts(id),
    merged_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_contacts_name ON contacts(name);
CREATE INDEX IF NOT EXISTS idx_contacts_company ON contacts(company);
CREATE INDEX IF NOT EXISTS idx_contacts_status ON contacts(status);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    client_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('idea', 'planning', 'active', 'paused', 'completed', 'cancelled')),
    priority TEXT DEFAULT 'medium'
        CHECK (priority IN ('low', 'medium', 'high', 'urgent')),
    start_date TEXT,
    target_date TEXT,
    completed_date TEXT,
    workspace_path TEXT,
    dev_port INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
CREATE INDEX IF NOT EXISTS idx_projects_client ON projects(client_id);
CREATE INDEX IF NOT EXISTS idx_projects_workspace
    ON projects(workspace_path) WHERE workspace_path IS NOT NULL;

-- Renamed from tasks per the schema doc (the bare name "tasks" is too generic
-- and clashes with the loci runner's internal task concept).
CREATE TABLE IF NOT EXISTS project_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'todo'
        CHECK (status IN ('todo', 'in_progress', 'done', 'blocked')),
    priority TEXT DEFAULT 'medium'
        CHECK (priority IN ('low', 'medium', 'high', 'urgent')),
    assigned_to INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    due_date TEXT,
    completed_at TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_project_tasks_project ON project_tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_project_tasks_status ON project_tasks(status);

CREATE TABLE IF NOT EXISTS milestones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    target_date TEXT,
    completed_date TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'completed', 'missed')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_milestones_project ON milestones(project_id);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    context TEXT,
    options_considered TEXT,
    decision TEXT NOT NULL,
    rationale TEXT,
    outcome TEXT,
    outcome_date TEXT,
    status TEXT NOT NULL DEFAULT 'decided'
        CHECK (status IN ('open', 'decided', 'revisit', 'validated', 'regretted')),
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    decided_at TEXT NOT NULL DEFAULT (datetime('now')),
    confidence_level INTEGER CHECK (confidence_level BETWEEN 1 AND 10),
    review_30_date TEXT,
    review_90_date TEXT,
    review_180_date TEXT,
    process_quality INTEGER CHECK (process_quality BETWEEN 1 AND 5),
    outcome_quality INTEGER CHECK (outcome_quality BETWEEN 1 AND 5),
    within_control TEXT,
    external_factors TEXT,
    would_do_differently TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_decisions_status ON decisions(status);
CREATE INDEX IF NOT EXISTS idx_decisions_project ON decisions(project_id);
CREATE INDEX IF NOT EXISTS idx_decisions_contact ON decisions(contact_id);
CREATE INDEX IF NOT EXISTS idx_decisions_date ON decisions(decided_at);

-- Kept as-is for V1. linked_contacts / linked_projects TEXT columns are
-- also backfilled into entity_edges by the seed script (step 4 on journal),
-- so loci_v2 walks via entity_edges instead of parsing TEXT at query time.
CREATE TABLE IF NOT EXISTS journal_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    mood TEXT,
    energy INTEGER CHECK (energy BETWEEN 1 AND 5),
    highlights TEXT,
    entry_date TEXT NOT NULL,
    linked_contacts TEXT,
    linked_projects TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_journal_date ON journal_entries(entry_date);
CREATE INDEX IF NOT EXISTS idx_journal_mood ON journal_entries(mood);

CREATE TABLE IF NOT EXISTS contact_interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    type TEXT NOT NULL
        CHECK (type IN ('email', 'call', 'meeting', 'message', 'other')),
    direction TEXT NOT NULL
        CHECK (direction IN ('inbound', 'outbound')),
    subject TEXT,
    summary TEXT,
    occurred_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_interactions_contact ON contact_interactions(contact_id);
CREATE INDEX IF NOT EXISTS idx_interactions_date ON contact_interactions(occurred_at);
CREATE INDEX IF NOT EXISTS idx_interactions_type ON contact_interactions(type);

-- Note: account_id FK to google_accounts is dropped here — next_soy does not
-- carry over google_accounts, so the column is preserved but the reference is
-- implicit (stored integer, not enforced).
CREATE TABLE IF NOT EXISTS emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gmail_id TEXT UNIQUE,
    thread_id TEXT,
    contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    from_address TEXT NOT NULL,
    from_name TEXT,
    to_addresses TEXT,
    subject TEXT,
    snippet TEXT,
    body_preview TEXT,
    labels TEXT,
    is_read INTEGER NOT NULL DEFAULT 0,
    is_starred INTEGER NOT NULL DEFAULT 0,
    received_at TEXT NOT NULL,
    account_id INTEGER,
    synced_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_emails_contact ON emails(contact_id);
CREATE INDEX IF NOT EXISTS idx_emails_thread ON emails(thread_id);
CREATE INDEX IF NOT EXISTS idx_emails_date ON emails(received_at);
CREATE INDEX IF NOT EXISTS idx_emails_gmail_id ON emails(gmail_id);
CREATE INDEX IF NOT EXISTS idx_emails_from ON emails(from_address);

CREATE TABLE IF NOT EXISTS calendar_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    google_event_id TEXT UNIQUE,
    calendar_id TEXT DEFAULT 'primary',
    title TEXT NOT NULL,
    description TEXT,
    location TEXT,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    all_day INTEGER NOT NULL DEFAULT 0,
    status TEXT DEFAULT 'confirmed'
        CHECK (status IN ('confirmed', 'tentative', 'cancelled')),
    attendees TEXT,
    contact_ids TEXT,
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    account_id INTEGER,
    synced_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_events_start ON calendar_events(start_time);
CREATE INDEX IF NOT EXISTS idx_events_google_id ON calendar_events(google_event_id);
CREATE INDEX IF NOT EXISTS idx_events_project ON calendar_events(project_id);

-- FKs to source_email_id / source_calendar_event_id kept logically; source_doc_id
-- remains a string ref to external Google Docs.
CREATE TABLE IF NOT EXISTS transcripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    source TEXT DEFAULT 'paste',
    raw_text TEXT NOT NULL,
    summary TEXT,
    duration_minutes INTEGER,
    occurred_at TEXT NOT NULL,
    processed_at TEXT,
    call_intelligence TEXT,
    source_email_id INTEGER REFERENCES emails(id),
    source_calendar_event_id INTEGER REFERENCES calendar_events(id),
    source_doc_id TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_transcripts_occurred ON transcripts(occurred_at);

CREATE TABLE IF NOT EXISTS transcript_participants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transcript_id INTEGER NOT NULL REFERENCES transcripts(id) ON DELETE CASCADE,
    contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    speaker_label TEXT NOT NULL,
    is_user INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_tp_transcript ON transcript_participants(transcript_id);
CREATE INDEX IF NOT EXISTS idx_tp_contact ON transcript_participants(contact_id);

CREATE TABLE IF NOT EXISTS commitments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transcript_id INTEGER NOT NULL REFERENCES transcripts(id) ON DELETE CASCADE,
    owner_contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    is_user_commitment INTEGER DEFAULT 0,
    description TEXT NOT NULL,
    deadline_mentioned TEXT,
    deadline_date TEXT,
    status TEXT DEFAULT 'open'
        CHECK(status IN ('open', 'completed', 'overdue', 'cancelled')),
    linked_task_id INTEGER,
    linked_project_id INTEGER,
    completed_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_commitments_transcript ON commitments(transcript_id);
CREATE INDEX IF NOT EXISTS idx_commitments_owner ON commitments(owner_contact_id);
CREATE INDEX IF NOT EXISTS idx_commitments_status ON commitments(status);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    color TEXT DEFAULT '#6b7280',
    category TEXT
);

CREATE TABLE IF NOT EXISTS entity_tags (
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (entity_type, entity_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_entity_tags_lookup ON entity_tags(entity_type, entity_id);

-- Polymorphic notes (attached to a specific entity) — kept as-is.
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_notes_entity ON notes(entity_type, entity_id);

-- ---------------------------------------------------------------------------
-- 2. New tables (entity_edges, notes_v2, contact_identities, daily_logs,
--    log_mentions, wikilinks, memory_episodes, episode_members)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS contact_identities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_contact_id INTEGER NOT NULL
        REFERENCES contacts(id) ON DELETE CASCADE,
    identity_type TEXT NOT NULL
        CHECK (identity_type IN (
            'email', 'phone', 'linkedin', 'github_handle',
            'discord_handle', 'alias_name', 'external_id'
        )),
    identity_value TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0
        CHECK (confidence >= 0.0 AND confidence <= 1.0),
    first_seen TEXT,
    last_seen TEXT,
    verified INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'manual'
        CHECK (source IN ('manual', 'backfill', 'import', 'merge')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (identity_type, identity_value)
);

CREATE INDEX IF NOT EXISTS idx_identities_canonical
    ON contact_identities(canonical_contact_id);
CREATE INDEX IF NOT EXISTS idx_identities_value
    ON contact_identities(identity_type, identity_value);

CREATE TABLE IF NOT EXISTS notes_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    content TEXT NOT NULL,
    note_kind TEXT NOT NULL DEFAULT 'freeform'
        CHECK (note_kind IN (
            'freeform', 'meeting', 'idea', 'decision_draft',
            'brief', 'observation', 'reference'
        )),
    pinned INTEGER NOT NULL DEFAULT 0,
    promoted_to_type TEXT
        CHECK (promoted_to_type IS NULL OR promoted_to_type IN (
            'decision', 'project', 'follow_up', 'task', 'milestone'
        )),
    promoted_to_id INTEGER,
    source TEXT NOT NULL DEFAULT 'manual'
        CHECK (source IN (
            'manual', 'daily_log_extract', 'email_clip',
            'voice_memo', 'import', 'migrated_from_standalone'
        )),
    source_ref TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_notes_v2_kind ON notes_v2(note_kind);
CREATE INDEX IF NOT EXISTS idx_notes_v2_pinned
    ON notes_v2(pinned) WHERE pinned = 1;
CREATE INDEX IF NOT EXISTS idx_notes_v2_promoted
    ON notes_v2(promoted_to_type, promoted_to_id)
    WHERE promoted_to_type IS NOT NULL;
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
        CHECK (mention_status IN ('resolved', 'suggested', 'rejected')),
    resolution_source TEXT NOT NULL
        CHECK (resolution_source IN (
            'wikilink', 'name_match', 'user_confirmed', 'llm_suggested'
        )),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_log_mentions_log ON log_mentions(log_id);
CREATE INDEX IF NOT EXISTS idx_log_mentions_entity
    ON log_mentions(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_log_mentions_status
    ON log_mentions(mention_status) WHERE mention_status != 'resolved';

CREATE TABLE IF NOT EXISTS wikilinks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alias TEXT NOT NULL,
    canonical_type TEXT NOT NULL,
    canonical_id INTEGER NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 1.0,
    created_by TEXT NOT NULL DEFAULT 'user'
        CHECK (created_by IN ('user', 'auto_name_match', 'import')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (alias, canonical_type, canonical_id)
);

CREATE INDEX IF NOT EXISTS idx_wikilinks_alias ON wikilinks(alias);
CREATE INDEX IF NOT EXISTS idx_wikilinks_entity
    ON wikilinks(canonical_type, canonical_id);

CREATE TABLE IF NOT EXISTS memory_episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    summary TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    episode_type TEXT
        CHECK (episode_type IN (
            'project_phase', 'relationship_phase', 'life_event',
            'conceptual_thread', 'user_defined'
        )),
    emotional_tone TEXT,
    created_by TEXT NOT NULL DEFAULT 'user'
        CHECK (created_by IN ('user', 'auto_cluster', 'import')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_episodes_active
    ON memory_episodes(started_at) WHERE ended_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_episodes_type ON memory_episodes(episode_type);

CREATE TABLE IF NOT EXISTS episode_members (
    episode_id INTEGER NOT NULL
        REFERENCES memory_episodes(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    role TEXT,
    added_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (episode_id, entity_type, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_episode_members_entity
    ON episode_members(entity_type, entity_id);

-- The load-bearing table: every loci walk consults this.
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
        CHECK (source IN ('manual', 'backfill', 'wikilink', 'import', 'merge', 'user_pin')),
    metadata TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (src_type, src_id, dst_type, dst_id, edge_type)
);

CREATE INDEX IF NOT EXISTS idx_edges_src
    ON entity_edges(src_type, src_id, edge_type);
CREATE INDEX IF NOT EXISTS idx_edges_dst
    ON entity_edges(dst_type, dst_id, edge_type);
CREATE INDEX IF NOT EXISTS idx_edges_type ON entity_edges(edge_type);
CREATE INDEX IF NOT EXISTS idx_edges_active
    ON entity_edges(src_type, src_id) WHERE ended_at IS NULL;

-- ---------------------------------------------------------------------------
-- 3. Schema marker so loci_v2 can assert it's reading the right DB.
-- ---------------------------------------------------------------------------

INSERT OR REPLACE INTO soy_meta (key, value)
    VALUES ('next_soy_schema_version', '001_core');
