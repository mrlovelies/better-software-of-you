-- Rejection inference queue — when humans reject without a reason,
-- the LLM figures out why by comparing the signal against approved ones.

CREATE TABLE IF NOT EXISTS rejection_inference_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    signal_type TEXT NOT NULL DEFAULT 'harvest',  -- 'harvest', 'competitive', 'forecast'
    status TEXT NOT NULL DEFAULT 'pending',        -- 'pending', 'processing', 'done', 'failed'
    inferred_reason TEXT,
    model_used TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    processed_at TEXT,
    UNIQUE(signal_id, signal_type)
);
