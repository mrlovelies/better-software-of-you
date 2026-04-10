-- Infrastructure Recommendation System
-- Evaluates ambient research findings against SoY architecture
-- and produces scored, actionable improvement recommendations.

-- Recommendations: the core pipeline output
CREATE TABLE IF NOT EXISTS infra_recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_id INTEGER REFERENCES research_findings(id),
    stream_id INTEGER REFERENCES research_streams(id),
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    relevance_score INTEGER,
    effort_score INTEGER,
    impact_score INTEGER,
    urgency_score INTEGER,
    risk_score INTEGER,
    composite_score REAL,
    target_files TEXT,
    proposed_changes TEXT,
    affected_modules TEXT,
    auto_eligible INTEGER DEFAULT 0,
    requires_review TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'implementing', 'implemented', 'failed', 'deferred')),
    tier_evaluated INTEGER,
    model_used TEXT,
    reviewed_at TEXT,
    reviewed_by TEXT,
    review_notes TEXT,
    handoff_id INTEGER REFERENCES session_handoffs(id),
    implemented_at TEXT,
    implementation_notes TEXT,
    architecture_hash TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Calibration: track human overrides to improve scoring
CREATE TABLE IF NOT EXISTS infra_calibration (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recommendation_id INTEGER NOT NULL REFERENCES infra_recommendations(id),
    dimension TEXT NOT NULL,
    model_score INTEGER,
    human_verdict TEXT,
    was_correct INTEGER,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Score weight evolution
CREATE TABLE IF NOT EXISTS infra_score_weights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dimension TEXT NOT NULL UNIQUE,
    weight REAL NOT NULL DEFAULT 1.0,
    approved_avg REAL,
    rejected_avg REAL,
    sample_count INTEGER DEFAULT 0,
    last_calibrated_at TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Evolution log
CREATE TABLE IF NOT EXISTS infra_evolution_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    change_type TEXT NOT NULL,
    description TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    reason TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Seed default weights
INSERT OR IGNORE INTO infra_score_weights (dimension, weight) VALUES ('relevance', 2.0);
INSERT OR IGNORE INTO infra_score_weights (dimension, weight) VALUES ('effort', 1.0);
INSERT OR IGNORE INTO infra_score_weights (dimension, weight) VALUES ('impact', 1.5);
INSERT OR IGNORE INTO infra_score_weights (dimension, weight) VALUES ('urgency', 1.5);
INSERT OR IGNORE INTO infra_score_weights (dimension, weight) VALUES ('risk', 1.0);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_infra_rec_status ON infra_recommendations(status);
CREATE INDEX IF NOT EXISTS idx_infra_rec_composite ON infra_recommendations(composite_score);
CREATE INDEX IF NOT EXISTS idx_infra_rec_category ON infra_recommendations(category);
CREATE INDEX IF NOT EXISTS idx_infra_rec_finding ON infra_recommendations(finding_id);
CREATE INDEX IF NOT EXISTS idx_infra_cal_rec ON infra_calibration(recommendation_id);
CREATE INDEX IF NOT EXISTS idx_infra_evo_type ON infra_evolution_log(change_type);
