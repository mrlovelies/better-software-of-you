-- Harvest Forecasts — creative product ideation from signal patterns
-- Generates ideas that aren't being directly signalled but emerge from
-- pattern analysis, adjacent problems, and silence gaps.

CREATE TABLE IF NOT EXISTS harvest_forecasts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL,

    -- Origin: how was this idea generated?
    origin_type TEXT NOT NULL,               -- 'pattern_synthesis', 'silence_gap', 'adjacent_problem',
                                             -- 'upstream_cause', 'automation_opportunity', 'human_idea'
    origin_signals TEXT,                     -- JSON array of signal IDs that inspired this (if any)
    origin_reasoning TEXT,                   -- why the system thinks this is viable

    -- Autonomy scoring: how hands-off can this be?
    autonomy_score INTEGER,                  -- 1-10: 10 = fully autonomous, 1 = heavy human involvement
    autonomy_breakdown TEXT,                 -- JSON: {"setup": 1-10, "operation": 1-10, "support": 1-10, "maintenance": 1-10}
    revenue_model TEXT,                      -- 'recurring_passive', 'recurring_active', 'one_time', 'usage_based'
    recurring_potential INTEGER,             -- 1-10: how much of the revenue is recurring/passive

    -- Market assessment (same dimensions as triage for comparability)
    market_size_score INTEGER,
    monetization_score INTEGER,
    build_complexity_score INTEGER,
    existing_solutions_score INTEGER,
    soy_leaf_fit_score INTEGER,
    composite_score REAL,

    -- Classification
    industry TEXT,
    build_type TEXT,                         -- 'soy_leaf', 'standalone_saas', 'api_service', 'chrome_extension',
                                             -- 'bot', 'marketplace', 'data_product', 'fiverr_service'
    target_audience TEXT,
    estimated_build_days INTEGER,
    estimated_mrr_low REAL,                  -- monthly recurring revenue estimate (low)
    estimated_mrr_high REAL,                 -- monthly recurring revenue estimate (high)

    -- Lifecycle
    status TEXT NOT NULL DEFAULT 'idea',     -- 'idea', 'evaluated', 'approved', 'building', 'shipped', 'killed'
    human_notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_forecasts_status ON harvest_forecasts(status);
CREATE INDEX IF NOT EXISTS idx_forecasts_autonomy ON harvest_forecasts(autonomy_score);
CREATE INDEX IF NOT EXISTS idx_forecasts_composite ON harvest_forecasts(composite_score);
CREATE INDEX IF NOT EXISTS idx_forecasts_origin ON harvest_forecasts(origin_type);
