-- Signal Harvester tables
-- Stores demand signals harvested from Reddit and other sources,
-- triage decisions, and build dispatch tracking.

CREATE TABLE IF NOT EXISTS harvest_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,              -- e.g. 'reddit', 'hackernews', 'twitter'
    source_type TEXT NOT NULL,              -- 'api', 'scrape', 'computer_use'
    config TEXT,                            -- JSON config (subreddits, search terms, etc.)
    enabled INTEGER DEFAULT 1,
    last_harvested_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS harvest_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES harvest_sources(id),
    source_url TEXT,                        -- permalink to the original post/comment
    source_author TEXT,                     -- reddit username, etc.
    platform TEXT NOT NULL,                 -- 'reddit', 'hn', 'twitter', etc.
    subreddit TEXT,                         -- for reddit signals
    raw_text TEXT NOT NULL,                 -- the original post/comment text
    signal_type TEXT,                       -- 'wish', 'complaint', 'question', 'frustration'
    extracted_pain TEXT,                    -- LLM-extracted pain point summary
    industry TEXT,                          -- detected industry/vertical
    upvotes INTEGER DEFAULT 0,
    comment_count INTEGER DEFAULT 0,
    harvested_at TEXT DEFAULT (datetime('now')),
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS harvest_triage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL REFERENCES harvest_signals(id),
    -- Scoring dimensions (1-10)
    market_size_score INTEGER,              -- how many people likely have this problem
    monetization_score INTEGER,             -- can someone charge for a solution
    build_complexity_score INTEGER,         -- how hard is it to build (lower = easier = better)
    existing_solutions_score INTEGER,       -- how well-served is this already (lower = more saturated)
    soy_leaf_fit_score INTEGER,             -- does this fit as a SoY module
    composite_score REAL,                   -- weighted aggregate
    -- LLM analysis
    existing_solutions TEXT,                -- known existing solutions
    monetization_model TEXT,                -- suggested pricing approach
    build_estimate TEXT,                    -- rough scope estimate
    target_audience TEXT,                   -- who would pay for this
    verdict TEXT NOT NULL DEFAULT 'pending', -- 'pending', 'approved', 'rejected', 'deferred'
    verdict_reason TEXT,
    -- Human gate
    human_reviewed INTEGER DEFAULT 0,
    human_notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS harvest_builds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    triage_id INTEGER NOT NULL REFERENCES harvest_triage(id),
    project_name TEXT NOT NULL,
    build_type TEXT,                        -- 'soy_leaf', 'standalone_app', 'fiverr_service', 'micro_saas'
    spec TEXT,                              -- JSON build specification
    status TEXT NOT NULL DEFAULT 'queued',  -- 'queued', 'building', 'review', 'shipped', 'failed'
    agent_framework TEXT,                   -- 'claude_code', 'ruflo', 'paperclip', 'manual'
    output_path TEXT,                       -- where the built thing lives
    deploy_url TEXT,                        -- if deployed somewhere
    revenue REAL DEFAULT 0,                 -- tracked revenue from this build
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_signals_platform ON harvest_signals(platform);
CREATE INDEX IF NOT EXISTS idx_signals_industry ON harvest_signals(industry);
CREATE INDEX IF NOT EXISTS idx_triage_verdict ON harvest_triage(verdict);
CREATE INDEX IF NOT EXISTS idx_triage_composite ON harvest_triage(composite_score);
CREATE INDEX IF NOT EXISTS idx_builds_status ON harvest_builds(status);
