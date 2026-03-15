-- Ambient Research & Intelligence Layer
-- Three-tier research pipeline: local 7B → local 14B → Claude CLI

-- Research streams: dynamic, evolving focus areas
CREATE TABLE IF NOT EXISTS research_streams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    keywords TEXT,  -- JSON array of search terms
    linked_project_ids TEXT,  -- JSON array of project IDs
    tier_1_cadence_hours REAL DEFAULT 6,
    tier_2_cadence_hours REAL DEFAULT 12,
    tier_3_cadence_hours REAL DEFAULT 168,  -- weekly
    priority INTEGER DEFAULT 5,  -- 1-10, higher = more important
    active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Task queue: dispatched research jobs
CREATE TABLE IF NOT EXISTS research_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stream_id INTEGER NOT NULL REFERENCES research_streams(id),
    tier INTEGER NOT NULL CHECK (tier IN (1, 2, 3)),
    task_type TEXT NOT NULL,  -- 'web_sweep', 'summarize', 'wiki_update', 'synthesize', 'digest'
    prompt TEXT NOT NULL,
    model TEXT,  -- e.g. 'mistral:7b', 'qwen2.5:14b', 'claude-cli'
    machine TEXT,  -- 'razer', 'lucy', 'local'
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'failed', 'skipped')),
    input_data TEXT,  -- JSON: references to findings, prior context
    output_data TEXT,  -- JSON: raw result
    tokens_in INTEGER,
    tokens_out INTEGER,
    duration_ms INTEGER,
    error TEXT,
    scheduled_at TEXT DEFAULT (datetime('now')),
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Findings: raw research results from Tier 1/2
CREATE TABLE IF NOT EXISTS research_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stream_id INTEGER NOT NULL REFERENCES research_streams(id),
    task_id INTEGER REFERENCES research_tasks(id),
    tier INTEGER NOT NULL,
    finding_type TEXT NOT NULL,  -- 'article', 'trend', 'tool', 'technique', 'news', 'insight'
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    source_url TEXT,
    relevance_score REAL,  -- 0-1, assigned by filtering model
    cross_stream_ids TEXT,  -- JSON array: other streams this is relevant to
    incorporated INTEGER DEFAULT 0,  -- has this been folded into the wiki?
    created_at TEXT DEFAULT (datetime('now'))
);

-- Wiki documents: living, evolving knowledge per stream
CREATE TABLE IF NOT EXISTS research_wikis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stream_id INTEGER NOT NULL REFERENCES research_streams(id),
    title TEXT NOT NULL,
    content TEXT NOT NULL,  -- markdown
    version INTEGER DEFAULT 1,
    word_count INTEGER,
    last_synthesized_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Wiki version history
CREATE TABLE IF NOT EXISTS research_wiki_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wiki_id INTEGER NOT NULL REFERENCES research_wikis(id),
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    change_summary TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Weekly digests
CREATE TABLE IF NOT EXISTS research_digests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start TEXT NOT NULL,  -- ISO date of Monday
    title TEXT,
    content TEXT NOT NULL,  -- markdown
    workshop_content TEXT,  -- learning module section
    streams_covered TEXT,  -- JSON array of stream IDs
    generated_by TEXT DEFAULT 'claude-cli',
    created_at TEXT DEFAULT (datetime('now'))
);

-- Stream activity tracking: what Alex worked on (feeds priority engine)
CREATE TABLE IF NOT EXISTS research_activity_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stream_id INTEGER REFERENCES research_streams(id),
    signal_type TEXT NOT NULL,  -- 'project_activity', 'question_asked', 'manual_boost', 'search_query'
    signal_data TEXT,  -- JSON context
    weight REAL DEFAULT 1.0,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Machine registry for the Ollama network
CREATE TABLE IF NOT EXISTS research_machines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    tailscale_ip TEXT NOT NULL,
    ollama_port INTEGER DEFAULT 11434,
    models TEXT,  -- JSON array of available models
    tier INTEGER NOT NULL,
    gpu TEXT,
    vram_mb INTEGER,
    active INTEGER DEFAULT 1,
    last_seen_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Register the module
INSERT OR IGNORE INTO modules (name, version, enabled)
VALUES ('ambient-research', '0.1.0', 1);
