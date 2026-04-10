-- Harvest Evolution — feedback loops for self-improving pipeline
-- Tracks performance at each stage so the system learns what works.

-- Query performance: which search queries yield signals that survive triage?
CREATE TABLE IF NOT EXISTS harvest_query_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL,
    source_name TEXT NOT NULL DEFAULT 'reddit',
    subreddit TEXT,                          -- NULL for global searches
    times_used INTEGER DEFAULT 0,
    signals_found INTEGER DEFAULT 0,         -- raw matches
    signals_passed_t1 INTEGER DEFAULT 0,     -- survived noise filter
    signals_passed_t2 INTEGER DEFAULT 0,     -- scored above threshold
    signals_approved INTEGER DEFAULT 0,      -- human approved
    signals_built INTEGER DEFAULT 0,         -- made it to build
    signals_shipped INTEGER DEFAULT 0,       -- shipped product
    revenue_generated REAL DEFAULT 0,        -- total revenue from builds sourced by this query
    yield_rate REAL,                         -- approved / found (computed)
    last_used_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(query, source_name, subreddit)
);

-- Pattern performance: which regex patterns catch real signals vs noise?
CREATE TABLE IF NOT EXISTS harvest_pattern_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT NOT NULL UNIQUE,
    times_matched INTEGER DEFAULT 0,
    true_positives INTEGER DEFAULT 0,        -- matched AND passed triage
    false_positives INTEGER DEFAULT 0,       -- matched BUT rejected as noise
    precision_rate REAL,                     -- tp / (tp + fp)
    last_matched_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Subreddit performance: which communities produce viable signals?
CREATE TABLE IF NOT EXISTS harvest_subreddit_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subreddit TEXT NOT NULL UNIQUE,
    signals_harvested INTEGER DEFAULT 0,
    signals_approved INTEGER DEFAULT 0,
    signals_shipped INTEGER DEFAULT 0,
    revenue_generated REAL DEFAULT 0,
    avg_composite_score REAL,
    yield_rate REAL,                         -- approved / harvested
    active INTEGER DEFAULT 1,                -- auto-disable low-yield subs
    last_harvested_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Triage calibration: track human overrides of LLM decisions
CREATE TABLE IF NOT EXISTS triage_calibration (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL REFERENCES harvest_signals(id),
    tier TEXT NOT NULL,                      -- 't1', 't2', 't3'
    model_used TEXT,                         -- which LLM model
    model_verdict TEXT,                      -- what the model said
    human_verdict TEXT,                      -- what the human decided
    was_correct INTEGER,                     -- 1 if model matched human, 0 if overridden
    composite_score_at_decision REAL,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Industry performance: which industries/verticals produce viable products?
CREATE TABLE IF NOT EXISTS harvest_industry_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    industry TEXT NOT NULL UNIQUE,
    signals_found INTEGER DEFAULT 0,
    signals_approved INTEGER DEFAULT 0,
    builds_attempted INTEGER DEFAULT 0,
    builds_shipped INTEGER DEFAULT 0,
    total_revenue REAL DEFAULT 0,
    avg_build_time_days REAL,
    success_rate REAL,                       -- shipped / attempted
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Evolution log: track when and how the system adapted
CREATE TABLE IF NOT EXISTS harvest_evolution_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage TEXT NOT NULL,                     -- 'harvest', 'triage', 'build', 'ship'
    change_type TEXT NOT NULL,               -- 'query_added', 'query_pruned', 'weight_adjusted',
                                            -- 'subreddit_added', 'subreddit_disabled',
                                            -- 'pattern_added', 'pattern_disabled',
                                            -- 'prompt_updated', 'threshold_adjusted'
    description TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    reason TEXT,                             -- data-driven rationale
    created_at TEXT DEFAULT (datetime('now'))
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_query_stats_yield ON harvest_query_stats(yield_rate);
CREATE INDEX IF NOT EXISTS idx_subreddit_yield ON harvest_subreddit_stats(yield_rate);
CREATE INDEX IF NOT EXISTS idx_calibration_correct ON triage_calibration(was_correct);
CREATE INDEX IF NOT EXISTS idx_industry_success ON harvest_industry_stats(success_rate);
CREATE INDEX IF NOT EXISTS idx_evolution_stage ON harvest_evolution_log(stage);
