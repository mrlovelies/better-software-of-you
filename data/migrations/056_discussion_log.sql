-- Discussion log for signals and forecasts
-- Accumulates human/AI feedback that can revise viability scores
CREATE TABLE IF NOT EXISTS harvest_discussions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,              -- 'signal' or 'forecast'
    entity_id INTEGER NOT NULL,             -- signal_id or forecast_id
    author TEXT NOT NULL,                   -- who posted (user name, 'system', 'claude')
    source TEXT DEFAULT 'dashboard',        -- 'dashboard', 'discord', 'cli'
    content TEXT NOT NULL,                  -- the discussion entry
    -- Optional score revisions (NULL if this entry doesn't revise scores)
    revised_scores TEXT,                    -- JSON of any revised score dimensions
    revised_composite REAL,                 -- new composite score (if revised)
    revision_rationale TEXT,                -- why scores were changed
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_discussions_entity ON harvest_discussions(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_discussions_created ON harvest_discussions(created_at);
