-- Learning Module
-- Daily digests and weekly workshops with interactive feedback and learning profile

-- Learning digests: daily educational recaps and weekly workshops
CREATE TABLE IF NOT EXISTS learning_digests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    digest_type TEXT NOT NULL CHECK (digest_type IN ('daily', 'weekly')),
    digest_date TEXT NOT NULL,
    title TEXT NOT NULL,
    sections TEXT NOT NULL,  -- JSON array of section objects
    sources TEXT,  -- JSON array of source references
    model TEXT,
    tokens_used INTEGER,
    generation_duration_ms INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(digest_type, digest_date)
);

-- Per-section feedback from the user
CREATE TABLE IF NOT EXISTS learning_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    digest_id INTEGER NOT NULL REFERENCES learning_digests(id) ON DELETE CASCADE,
    section_id TEXT NOT NULL,
    reaction TEXT NOT NULL CHECK (reaction IN ('got_it', 'tell_me_more', 'too_basic', 'too_advanced', 'this_clicked')),
    comment TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Learning profile: calibration data built from feedback
CREATE TABLE IF NOT EXISTS learning_profile (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,  -- 'depth', 'style', 'domain', 'pace'
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(category, key)
);

-- Learning stats view
CREATE VIEW IF NOT EXISTS v_learning_stats AS
SELECT
    (SELECT COUNT(*) FROM learning_digests WHERE digest_type = 'daily') as daily_count,
    (SELECT COUNT(*) FROM learning_digests WHERE digest_type = 'weekly') as weekly_count,
    (SELECT COUNT(*) FROM learning_feedback) as total_feedback,
    (SELECT COUNT(*) FROM learning_feedback WHERE reaction = 'got_it') as got_it_count,
    (SELECT COUNT(*) FROM learning_feedback WHERE reaction = 'tell_me_more') as tell_me_more_count,
    (SELECT COUNT(*) FROM learning_feedback WHERE reaction = 'too_basic') as too_basic_count,
    (SELECT COUNT(*) FROM learning_feedback WHERE reaction = 'too_advanced') as too_advanced_count,
    (SELECT COUNT(*) FROM learning_feedback WHERE reaction = 'this_clicked') as this_clicked_count,
    (SELECT MAX(created_at) FROM learning_digests) as last_digest_at;

-- Register the module
INSERT OR IGNORE INTO modules (name, version, enabled)
VALUES ('learning', '0.1.0', 1);
