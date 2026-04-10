-- Competitive Intelligence — harvest dissatisfaction with existing products/services
-- Separate from pain-point harvesting: this targets NAMED products being trashed.
-- "I switched from X because...", "X sucks at...", "if only X could..."

CREATE TABLE IF NOT EXISTS competitive_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_url TEXT,
    source_author TEXT,
    platform TEXT NOT NULL,
    subreddit TEXT,
    raw_text TEXT NOT NULL,

    -- The product being complained about
    target_product TEXT,                     -- e.g. "Notion", "QuickBooks", "Shein"
    target_company TEXT,
    target_category TEXT,                    -- e.g. "project management", "accounting", "fast fashion"
    target_url TEXT,                         -- product website if known
    target_pricing TEXT,                     -- what the existing product charges

    -- The complaint
    complaint_type TEXT,                     -- 'missing_feature', 'poor_quality', 'overpriced',
                                            -- 'bad_ux', 'privacy_concern', 'reliability',
                                            -- 'poor_support', 'abandoned', 'bait_and_switch'
    complaint_summary TEXT,                 -- LLM-extracted summary of what's wrong
    missing_features TEXT,                  -- JSON array of specific features people want
    sentiment_intensity INTEGER,            -- 1-10: how angry/frustrated are people

    -- Engagement signals
    upvotes INTEGER DEFAULT 0,
    comment_count INTEGER DEFAULT 0,
    agreement_ratio REAL,                   -- estimated % of commenters who agree

    -- Analysis
    market_size_score INTEGER,              -- how big is this product's market
    switchability_score INTEGER,            -- 1-10: how easy is it for users to switch
    build_advantage_score INTEGER,          -- 1-10: how much better could we make it
    revenue_opportunity_score INTEGER,      -- 1-10: can we capture their revenue
    composite_score REAL,

    -- Lifecycle
    verdict TEXT DEFAULT 'pending',         -- 'pending', 'opportunity', 'rejected', 'building'
    human_reviewed INTEGER DEFAULT 0,
    human_notes TEXT,

    harvested_at TEXT DEFAULT (datetime('now')),
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Track which products are accumulating the most complaints
CREATE TABLE IF NOT EXISTS competitive_targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name TEXT NOT NULL,
    company TEXT,
    category TEXT,
    url TEXT,
    pricing TEXT,
    total_complaints INTEGER DEFAULT 0,
    avg_sentiment_intensity REAL,
    top_complaint_types TEXT,               -- JSON: most common complaint types
    top_missing_features TEXT,              -- JSON: most requested missing features
    opportunity_score REAL,                 -- aggregate opportunity
    status TEXT DEFAULT 'watching',         -- 'watching', 'researching', 'targeting', 'building', 'shipped'
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(product_name, company)
);

CREATE INDEX IF NOT EXISTS idx_comp_signals_product ON competitive_signals(target_product);
CREATE INDEX IF NOT EXISTS idx_comp_signals_category ON competitive_signals(target_category);
CREATE INDEX IF NOT EXISTS idx_comp_signals_type ON competitive_signals(complaint_type);
CREATE INDEX IF NOT EXISTS idx_comp_signals_composite ON competitive_signals(composite_score);
CREATE INDEX IF NOT EXISTS idx_comp_targets_score ON competitive_targets(opportunity_score);
