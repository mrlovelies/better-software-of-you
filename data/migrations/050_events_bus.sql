-- 050: Unified event bus — the context spine for all agents, modules, and consumers.
-- Every pipeline, bot, sync, and interactive session emits events here.
-- The learning module, health sweeps, Discord nudges, and dashboards all read from this single table.

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,       -- 'agent_started', 'agent_completed', 'agent_failed', 'error',
                                    -- 'email_received', 'commit_pushed', 'session_started',
                                    -- 'signal_harvested', 'digest_generated', 'discord_message',
                                    -- 'calendar_event', 'handoff_created', 'build_completed',
                                    -- 'sync_completed', 'health_check'
    source TEXT NOT NULL,           -- 'gmail', 'git', 'claude-code', 'discord', 'telegram',
                                    -- 'signal-harvester', 'learning', 'calendar', 'health-sweep',
                                    -- 'ambient-research', 'platform-health'
    entity_type TEXT,               -- 'contact', 'project', 'signal', 'email', 'digest', etc.
    entity_id INTEGER,
    summary TEXT NOT NULL,          -- human-readable one-liner
    metadata TEXT,                  -- JSON blob for source-specific details
    machine TEXT,                   -- hostname that emitted the event
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_events_type_date ON events(event_type, created_at);
CREATE INDEX IF NOT EXISTS idx_events_source_date ON events(source, created_at);
CREATE INDEX IF NOT EXISTS idx_events_entity ON events(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);
