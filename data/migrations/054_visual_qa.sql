-- Visual QA verdict column on harvest_builds
ALTER TABLE harvest_builds ADD COLUMN visual_qa_verdict TEXT;
ALTER TABLE harvest_builds ADD COLUMN visual_qa_score REAL;
ALTER TABLE harvest_builds ADD COLUMN visual_qa_report_path TEXT;

CREATE INDEX IF NOT EXISTS idx_builds_visual_qa ON harvest_builds(visual_qa_verdict);
