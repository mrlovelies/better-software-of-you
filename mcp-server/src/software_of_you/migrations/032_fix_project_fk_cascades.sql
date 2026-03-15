-- Fix project_id foreign keys that are missing ON DELETE SET NULL.
-- SQLite cannot ALTER constraints, so we recreate affected tables.

-- ── commitments: add proper FK on linked_project_id ──
CREATE TABLE commitments_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transcript_id INTEGER NOT NULL REFERENCES transcripts(id) ON DELETE CASCADE,
    owner_contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    is_user_commitment INTEGER DEFAULT 0,
    description TEXT NOT NULL,
    deadline_mentioned TEXT,
    deadline_date TEXT,
    status TEXT DEFAULT 'open' CHECK(status IN ('open', 'completed', 'overdue', 'cancelled')),
    linked_task_id INTEGER,
    linked_project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    completed_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
INSERT INTO commitments_new SELECT * FROM commitments;
DROP TABLE commitments;
ALTER TABLE commitments_new RENAME TO commitments;
CREATE INDEX idx_commitments_transcript ON commitments(transcript_id);
CREATE INDEX idx_commitments_owner ON commitments(owner_contact_id);
CREATE INDEX idx_commitments_status ON commitments(status);
CREATE INDEX idx_commitments_task ON commitments(linked_task_id);
CREATE INDEX idx_commitments_project ON commitments(linked_project_id);

-- ── telegram_dev_sessions: add ON DELETE SET NULL ──
CREATE TABLE telegram_dev_sessions_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL UNIQUE,
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    project_name TEXT NOT NULL,
    workspace_path TEXT NOT NULL,
    instruction TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'completed', 'failed', 'timeout', 'killed')),
    model TEXT NOT NULL DEFAULT 'sonnet',
    pid INTEGER,
    stdout_path TEXT,
    output_summary TEXT,
    git_diff_stat TEXT,
    git_before_sha TEXT,
    exit_code INTEGER,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    duration_seconds INTEGER,
    telegram_chat_id TEXT,
    branch_name TEXT,
    preview_url TEXT,
    deploy_status TEXT DEFAULT NULL
        CHECK (deploy_status IS NULL OR deploy_status IN ('deploying', 'deployed', 'deploy_failed')),
    deploy_pid INTEGER,
    deploy_stdout_path TEXT,
    review_status TEXT DEFAULT NULL
        CHECK (review_status IS NULL OR review_status IN ('pending', 'approved', 'rejected'))
);
INSERT INTO telegram_dev_sessions_new SELECT * FROM telegram_dev_sessions;
DROP TABLE telegram_dev_sessions;
ALTER TABLE telegram_dev_sessions_new RENAME TO telegram_dev_sessions;
CREATE INDEX idx_tg_dev_status ON telegram_dev_sessions(status);
CREATE INDEX idx_tg_dev_started ON telegram_dev_sessions(started_at);

-- ── income_records: add ON DELETE SET NULL ──
CREATE TABLE income_records_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'CAD',
    source TEXT NOT NULL,
    category TEXT NOT NULL
        CHECK (category IN ('vo_commercial', 'freelance', 'employment', 'residual', 'other')),
    description TEXT,
    reference_number TEXT,
    tax_year INTEGER NOT NULL,
    received_date TEXT,
    invoice_date TEXT,
    contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    agent_fee_pct REAL,
    agent_fee_amount REAL,
    net_amount REAL,
    tax_withheld REAL DEFAULT 0,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT INTO income_records_new SELECT * FROM income_records;
DROP TABLE income_records;
ALTER TABLE income_records_new RENAME TO income_records;
CREATE INDEX idx_income_tax_year ON income_records(tax_year);
CREATE INDEX idx_income_category ON income_records(category);
CREATE INDEX idx_income_source ON income_records(source);
CREATE INDEX idx_income_contact ON income_records(contact_id);
