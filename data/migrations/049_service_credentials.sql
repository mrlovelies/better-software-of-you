-- Service Credentials Vault — stores provisioned credentials for pipeline builds
-- Credentials are stored per-build and per-service, with the actual secrets
-- kept in the value field. In production, these should be encrypted.

CREATE TABLE IF NOT EXISTS service_credentials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    build_id TEXT,                           -- workspace slug or build identifier
    service TEXT NOT NULL,                   -- 'cloudflare_pages', 'cloudflare_d1', 'google_oauth', 'stripe', etc.
    key TEXT NOT NULL,                       -- credential key (e.g. 'client_id', 'api_key', 'database_id')
    value TEXT NOT NULL,                     -- credential value
    metadata TEXT,                           -- JSON metadata (project name, URLs, etc.)
    expires_at TEXT,                         -- optional expiry
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(build_id, service, key)
);

-- Global credentials (not per-build) — API tokens, account-level keys
CREATE TABLE IF NOT EXISTS service_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service TEXT NOT NULL UNIQUE,            -- 'cloudflare', 'google_cloud', 'stripe', 'github'
    account_id TEXT,                         -- account/org identifier
    config TEXT,                             -- JSON config (default project, region, etc.)
    status TEXT DEFAULT 'active',            -- 'active', 'expired', 'disabled'
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_creds_build ON service_credentials(build_id);
CREATE INDEX IF NOT EXISTS idx_creds_service ON service_credentials(service);
