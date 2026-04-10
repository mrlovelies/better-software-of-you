-- Google Docs Module Schema v1
-- Tracks Google Docs linked to contacts, projects, and generated exports

CREATE TABLE IF NOT EXISTS google_docs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    google_doc_id TEXT UNIQUE NOT NULL,
    title TEXT,
    url TEXT,
    account_id INTEGER REFERENCES google_accounts(id) ON DELETE SET NULL,
    contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    doc_type TEXT NOT NULL DEFAULT 'general' CHECK (doc_type IN ('general', 'brief', 'report', 'shared', 'export')),
    content_preview TEXT,
    last_modified_at TEXT,
    last_synced_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_google_docs_doc_id ON google_docs(google_doc_id);
CREATE INDEX IF NOT EXISTS idx_google_docs_contact ON google_docs(contact_id);
CREATE INDEX IF NOT EXISTS idx_google_docs_project ON google_docs(project_id);
CREATE INDEX IF NOT EXISTS idx_google_docs_type ON google_docs(doc_type);
CREATE INDEX IF NOT EXISTS idx_google_docs_account ON google_docs(account_id);

-- Register module
INSERT OR REPLACE INTO modules (name, version) VALUES ('google-docs', '1.0.0');
