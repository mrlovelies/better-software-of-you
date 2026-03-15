-- Add workspace_path to projects for filesystem integration
ALTER TABLE projects ADD COLUMN workspace_path TEXT;
CREATE INDEX IF NOT EXISTS idx_projects_workspace ON projects(workspace_path) WHERE workspace_path IS NOT NULL;
