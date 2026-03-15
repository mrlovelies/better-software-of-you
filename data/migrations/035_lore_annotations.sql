-- Lore Annotations — bidirectional creative dialogue on context entries.
-- Annotations can be corrections, questions, ideas, or observations from user or AI.

CREATE TABLE IF NOT EXISTS lore_annotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    context_id INTEGER NOT NULL,            -- FK to creative_context
    highlighted_text TEXT NOT NULL,          -- the selected passage
    note TEXT NOT NULL,                      -- the annotation content
    annotation_type TEXT NOT NULL DEFAULT 'observation'
        CHECK (annotation_type IN ('correction', 'question', 'idea', 'observation')),
    author TEXT NOT NULL DEFAULT 'user'      -- 'user' or 'ai'
        CHECK (author IN ('user', 'ai')),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'verified', 'revised', 'noted', 'dismissed')),
    research_response TEXT,                  -- AI research findings (for user annotations)
    revision_made TEXT,                      -- what changed in the source entry, if anything
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    reviewed_at TEXT,
    FOREIGN KEY (context_id) REFERENCES creative_context(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_lore_annotations_context ON lore_annotations(context_id);
CREATE INDEX IF NOT EXISTS idx_lore_annotations_status ON lore_annotations(status);
CREATE INDEX IF NOT EXISTS idx_lore_annotations_type ON lore_annotations(annotation_type);
CREATE INDEX IF NOT EXISTS idx_lore_annotations_author ON lore_annotations(author);

-- Ongoing Thoughts — freestanding creative dialogue not attached to a specific entry.
-- A persistent conversation space on the dashboard.

CREATE TABLE IF NOT EXISTS creative_threads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,                      -- optional FK to projects
    author TEXT NOT NULL DEFAULT 'ai'        -- who started this thread
        CHECK (author IN ('user', 'ai')),
    thread_type TEXT NOT NULL DEFAULT 'question'
        CHECK (thread_type IN ('question', 'provocation', 'observation', 'idea')),
    prompt TEXT NOT NULL,                     -- the question or thought
    response TEXT,                            -- the reply (from the other party)
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'answered', 'discussed', 'archived')),
    tags TEXT,                                -- comma-separated tags
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_creative_threads_project ON creative_threads(project_id);
CREATE INDEX IF NOT EXISTS idx_creative_threads_status ON creative_threads(status);
