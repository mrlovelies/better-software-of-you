-- Session Handoffs — cross-interface context continuity.
-- Stores structured handoff documents so any SoY interface (Telegram, Hub, etc.)
-- can pick up where another left off.

CREATE TABLE IF NOT EXISTS session_handoffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    summary TEXT NOT NULL,                          -- full handoff markdown
    project_ids TEXT,                                -- JSON array of project IDs touched
    branch TEXT,                                     -- git branch at time of handoff
    source TEXT NOT NULL DEFAULT 'claude-code',      -- which interface created it
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'picked_up', 'expired')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    picked_up_at TEXT,
    picked_up_by TEXT                                -- which interface consumed it
);

CREATE INDEX IF NOT EXISTS idx_session_handoffs_status ON session_handoffs(status);
CREATE INDEX IF NOT EXISTS idx_session_handoffs_created ON session_handoffs(created_at);

-- Auto-expire old handoffs: when a new one is created, the previous active ones
-- become stale. This is handled in application logic, not a trigger, to keep it simple.
