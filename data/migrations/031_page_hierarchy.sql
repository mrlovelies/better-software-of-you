-- Add parent page hierarchy to generated_views
ALTER TABLE generated_views ADD COLUMN parent_page_id INTEGER REFERENCES generated_views(id);
CREATE INDEX IF NOT EXISTS idx_generated_views_parent ON generated_views(parent_page_id);
