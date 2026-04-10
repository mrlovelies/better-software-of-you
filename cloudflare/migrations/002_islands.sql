-- Migration 002: Island form submissions table
-- Run: npx wrangler d1 execute soy-shared --remote --file=migrations/002_islands.sql

CREATE TABLE IF NOT EXISTS island_submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_token TEXT NOT NULL,
    island_id TEXT NOT NULL,
    form_data TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_island_submissions_token ON island_submissions(page_token);
CREATE INDEX IF NOT EXISTS idx_island_submissions_island ON island_submissions(island_id);
