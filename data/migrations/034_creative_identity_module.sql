-- Creative Identity Module — persistent writing style, narrative principles, and project creative context.
-- Three layers: mechanical baseline (from samples), narrative DNA (weighted principles), project context (lore/continuity).

-- Layer 1: Writing samples with computed mechanical metrics
CREATE TABLE IF NOT EXISTS writing_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'user_written'
        CHECK (source_type IN ('user_written', 'ai_approved', 'ai_rejected', 'reference')),
    content TEXT NOT NULL,
    project_id INTEGER,                  -- optional link to SoY project
    word_count INTEGER,
    sentence_count INTEGER,
    avg_sentence_length REAL,            -- words per sentence
    dialogue_word_count INTEGER,         -- words inside dialogue
    dialogue_ratio REAL,                 -- dialogue_word_count / word_count
    paragraph_count INTEGER,
    avg_paragraph_length REAL,           -- sentences per paragraph
    question_count INTEGER,
    exclamation_count INTEGER,
    italics_count INTEGER,               -- markdown *italic* occurrences
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_writing_samples_source ON writing_samples(source_type);
CREATE INDEX IF NOT EXISTS idx_writing_samples_project ON writing_samples(project_id);

-- Layer 2: Narrative principles — weighted creative preferences
CREATE TABLE IF NOT EXISTS narrative_principles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL
        CHECK (category IN ('structure', 'pacing', 'character', 'theme', 'pov', 'tone', 'dialogue', 'general')),
    principle TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 0.7     -- 0.0 to 1.0, how strongly this should influence output
        CHECK (weight >= 0.0 AND weight <= 1.0),
    evidence TEXT,                        -- the feedback or observation that led to this
    source_session TEXT,                  -- when/where this was observed
    active INTEGER NOT NULL DEFAULT 1,   -- soft delete / disable without losing history
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_narrative_principles_category ON narrative_principles(category);
CREATE INDEX IF NOT EXISTS idx_narrative_principles_active ON narrative_principles(active);

-- Layer 3: Per-project creative context (lore, characters, scenes, decisions, threads)
CREATE TABLE IF NOT EXISTS creative_context (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,                  -- links to SoY projects table
    context_type TEXT NOT NULL
        CHECK (context_type IN (
            'character', 'structure', 'theme', 'scene', 'decision',
            'thread', 'canon', 'lore', 'relationship', 'note'
        )),
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'resolved', 'deprecated', 'draft', 'complete')),
    tags TEXT,                            -- comma-separated tags for filtering
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_creative_context_project ON creative_context(project_id);
CREATE INDEX IF NOT EXISTS idx_creative_context_type ON creative_context(context_type);
CREATE INDEX IF NOT EXISTS idx_creative_context_status ON creative_context(status);

-- Post-session capture log
CREATE TABLE IF NOT EXISTS creative_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    session_date TEXT NOT NULL DEFAULT (date('now')),
    observations TEXT,                    -- what was learned about style/approach
    decisions_made TEXT,                  -- creative decisions finalized
    open_questions TEXT,                  -- unresolved threads
    scenes_worked TEXT,                   -- what scenes were drafted/revised
    mode_used TEXT DEFAULT 'raw'
        CHECK (mode_used IN ('learned', 'exploratory', 'raw')),
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_creative_sessions_project ON creative_sessions(project_id);

-- Seed narrative principles from first creative session
INSERT OR IGNORE INTO narrative_principles (category, principle, weight, evidence, source_session) VALUES
    ('structure', 'Indirect revelation over direct introspection — let the reader piece together emotional truth from observed behavior rather than stated feeling', 0.9, 'First drafting session: the scalp tingle came from Braska observing Auron''s architecture and seeing how it would be dismantled through a smile', '2026-03 Braska''s Pilgrimage session 1'),
    ('structure', 'Dramatic irony as primary emotional engine — reader knowledge doing work the prose doesn''t state', 0.9, 'Core structural principle identified in first session — the reader knowing where the pilgrimage ends makes every warm moment devastating', '2026-03 Braska''s Pilgrimage session 1'),
    ('pacing', 'Earned interiority — characters shouldn''t be overly introspective before the narrative earns it. Restraint early, depth later.', 0.8, 'Pacing instinct from first session — early chapters should build through external observation, interior access is a reward the prose earns', '2026-03 Braska''s Pilgrimage session 1'),
    ('character', 'Character opacity as a tool — some characters are more powerful when never given POV, described only through the disruption they cause in others', 0.85, 'Jecht never gets POV in the pilgrimage structure — he is always described through the reactions he provokes in Auron and Braska', '2026-03 Braska''s Pilgrimage session 1'),
    ('character', '"Correct" as containment system — character voice using specific repeated words as psychological architecture', 0.8, 'Auron''s use of "correct" as emotional armor identified in first drafting session', '2026-03 Braska''s Pilgrimage session 1'),
    ('structure', 'Closing paragraphs that land through nostalgic anticipation + empathy simultaneously', 0.85, 'Pattern identified across multiple draft endings in session 1', '2026-03 Braska''s Pilgrimage session 1'),
    ('pov', 'Observer POV can be more emotionally devastating than internal POV — Braska watching Auron can hit harder than being inside Auron''s head', 0.85, 'Discovery from comparing three POV versions of the same scene', '2026-03 Braska''s Pilgrimage session 1'),
    ('tone', 'Sentimental yet aggressive — these registers don''t need to resolve into one tone. Let them coexist.', 0.8, 'Tonal identity of the project — tenderness and violence existing in the same narrative space without flattening either', '2026-03 Braska''s Pilgrimage session 1');

-- Register module
INSERT OR REPLACE INTO modules (name, version, enabled)
VALUES ('creative_identity', '1.0.0', 1);
