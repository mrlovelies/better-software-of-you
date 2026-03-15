-- Audition Management Module
-- Tracks auditions from casting platforms (Casting Workbook, Actors Access, WeAudition)
-- and manual entries. Includes dedup table for email-based ingestion.

CREATE TABLE IF NOT EXISTS auditions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- What
    project_name TEXT NOT NULL,
    role_name TEXT,
    role_type TEXT,                    -- lead/supporting/guest/background/voiceover
    production_type TEXT,              -- tv/film/commercial/audiobook/theatre/other
    -- Who
    casting_director TEXT,
    casting_company TEXT,
    agent_contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    -- Source
    source TEXT NOT NULL DEFAULT 'manual',  -- castingworkbook/actorsaccess/weaudition/backstage/castingcallclub/manual
    source_email_id INTEGER REFERENCES emails(id) ON DELETE SET NULL,
    source_url TEXT,                   -- link to casting page if available
    -- Status
    status TEXT NOT NULL DEFAULT 'new' CHECK (status IN (
        'new', 'reviewing', 'preparing', 'recorded', 'submitted', 'callback', 'booked', 'passed', 'expired'
    )),
    -- Dates
    received_at TEXT,
    deadline TEXT,
    submitted_at TEXT,
    callback_date TEXT,
    -- Details
    notes TEXT,
    self_tape_specs TEXT,              -- format requirements, scenes, etc.
    sides_url TEXT,                    -- link to sides/script
    -- Timestamps
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audition_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audition_id INTEGER NOT NULL REFERENCES auditions(id) ON DELETE CASCADE,
    email_id INTEGER REFERENCES emails(id) ON DELETE SET NULL,
    source_type TEXT NOT NULL DEFAULT 'casting_email',
    fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_audition_sources_email ON audition_sources(email_id);
CREATE INDEX IF NOT EXISTS idx_auditions_status ON auditions(status);
CREATE INDEX IF NOT EXISTS idx_auditions_deadline ON auditions(deadline);
CREATE INDEX IF NOT EXISTS idx_auditions_source_email ON auditions(source_email_id);

-- Register module
INSERT OR REPLACE INTO modules (name, version, enabled, installed_at)
VALUES ('auditions', '1.0.0', 1, datetime('now'));
