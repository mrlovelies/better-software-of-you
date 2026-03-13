-- Writing Module — versioned draft management with feedback, character tagging, and lore cross-referencing.
-- Works for any project. Designed to complement the Creative Identity module.

-- Core draft units: chapters, scenes, fragments, poems, essays, etc.
-- Supports nesting via parent_id (e.g., scenes within chapters).
CREATE TABLE IF NOT EXISTS writing_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,                       -- FK to SoY project
    parent_id INTEGER,                        -- FK to self (nesting: scene → chapter)
    title TEXT NOT NULL,
    draft_type TEXT NOT NULL DEFAULT 'scene'
        CHECK (draft_type IN ('chapter', 'scene', 'fragment', 'poem', 'essay', 'section', 'other')),
    sort_order INTEGER NOT NULL DEFAULT 0,    -- ordering within parent
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('outline', 'draft', 'revision', 'review', 'final', 'archived')),
    current_version INTEGER NOT NULL DEFAULT 0, -- latest version number
    pov_character TEXT,                        -- primary POV for this draft
    synopsis TEXT,                             -- what this draft covers
    tags TEXT,                                 -- comma-separated tags
    word_count INTEGER DEFAULT 0,             -- mirrors current version word count
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL,
    FOREIGN KEY (parent_id) REFERENCES writing_drafts(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_writing_drafts_project ON writing_drafts(project_id);
CREATE INDEX IF NOT EXISTS idx_writing_drafts_parent ON writing_drafts(parent_id);
CREATE INDEX IF NOT EXISTS idx_writing_drafts_status ON writing_drafts(status);
CREATE INDEX IF NOT EXISTS idx_writing_drafts_type ON writing_drafts(draft_type);
CREATE INDEX IF NOT EXISTS idx_writing_drafts_pov ON writing_drafts(pov_character);

-- Version history: every save creates a new version. Content is never lost.
CREATE TABLE IF NOT EXISTS draft_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL,
    version_number INTEGER NOT NULL,
    content TEXT NOT NULL,                     -- the actual prose
    word_count INTEGER NOT NULL DEFAULT 0,
    change_summary TEXT,                       -- what changed in this version
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (draft_id) REFERENCES writing_drafts(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_draft_versions_unique ON draft_versions(draft_id, version_number);
CREATE INDEX IF NOT EXISTS idx_draft_versions_draft ON draft_versions(draft_id);

-- Structured feedback per draft (optionally pinned to a version).
-- Queryable by type, author, status.
CREATE TABLE IF NOT EXISTS draft_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL,
    version_number INTEGER,                   -- NULL = applies to draft generally
    feedback_type TEXT NOT NULL DEFAULT 'note'
        CHECK (feedback_type IN ('note', 'revision', 'critique', 'suggestion', 'question')),
    author TEXT NOT NULL DEFAULT 'user'
        CHECK (author IN ('user', 'ai', 'editor')),
    highlighted_text TEXT,                    -- optional: the passage this refers to
    content TEXT NOT NULL,                    -- the feedback itself
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'addressed', 'dismissed', 'deferred')),
    resolution TEXT,                          -- what was done about it
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at TEXT,
    FOREIGN KEY (draft_id) REFERENCES writing_drafts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_draft_feedback_draft ON draft_feedback(draft_id);
CREATE INDEX IF NOT EXISTS idx_draft_feedback_status ON draft_feedback(status);
CREATE INDEX IF NOT EXISTS idx_draft_feedback_type ON draft_feedback(feedback_type);
CREATE INDEX IF NOT EXISTS idx_draft_feedback_author ON draft_feedback(author);

-- Cross-references between drafts and creative_context entries (lore, characters, etc.)
CREATE TABLE IF NOT EXISTS draft_lore_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL,
    context_id INTEGER NOT NULL,              -- FK to creative_context
    link_type TEXT NOT NULL DEFAULT 'references'
        CHECK (link_type IN ('references', 'establishes', 'contradicts', 'extends')),
    note TEXT,                                -- why this link exists
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (draft_id) REFERENCES writing_drafts(id) ON DELETE CASCADE,
    FOREIGN KEY (context_id) REFERENCES creative_context(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_draft_lore_links_draft ON draft_lore_links(draft_id);
CREATE INDEX IF NOT EXISTS idx_draft_lore_links_context ON draft_lore_links(context_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_draft_lore_links_unique ON draft_lore_links(draft_id, context_id, link_type);

-- Character tagging per draft — who appears, in what role.
CREATE TABLE IF NOT EXISTS draft_characters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL,
    character_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'featured'
        CHECK (role IN ('pov', 'featured', 'mentioned', 'absent')),
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (draft_id) REFERENCES writing_drafts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_draft_characters_draft ON draft_characters(draft_id);
CREATE INDEX IF NOT EXISTS idx_draft_characters_name ON draft_characters(character_name);
CREATE INDEX IF NOT EXISTS idx_draft_characters_role ON draft_characters(role);
CREATE UNIQUE INDEX IF NOT EXISTS idx_draft_characters_unique ON draft_characters(draft_id, character_name);

-- Register module
INSERT OR REPLACE INTO modules (name, version, enabled, installed_at)
VALUES ('writing', '1.0.0', 1, datetime('now'));
