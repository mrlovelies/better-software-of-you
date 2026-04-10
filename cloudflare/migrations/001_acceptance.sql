-- Migration 001: Add acceptance columns to pages table
-- Run: npx wrangler d1 execute soy-shared --remote --file=migrations/001_acceptance.sql
--
-- Enables the "yes → instant live" flow for Specsite outreach.
-- acceptance_token is a UUID sent in the outreach email.
-- is_live flips to 1 when the prospect clicks "Go Live".

ALTER TABLE pages ADD COLUMN is_live INTEGER NOT NULL DEFAULT 0;
ALTER TABLE pages ADD COLUMN acceptance_token TEXT;
CREATE INDEX IF NOT EXISTS idx_pages_acceptance ON pages(acceptance_token);
CREATE INDEX IF NOT EXISTS idx_pages_is_live ON pages(is_live);
