-- Discord bot tables — mirrors Telegram bot infrastructure for Discord interface.
-- Conversations and sessions stored locally since the bot runs on this machine.

-- Conversation history for claude -p context
CREATE TABLE IF NOT EXISTS discord_conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    discord_message_id TEXT,
    channel_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_dc_conv_session ON discord_conversations(session_id);
CREATE INDEX IF NOT EXISTS idx_dc_conv_created ON discord_conversations(created_at);
CREATE INDEX IF NOT EXISTS idx_dc_conv_channel ON discord_conversations(channel_id);

-- Bot sessions (group messages by time proximity)
CREATE TABLE IF NOT EXISTS discord_bot_sessions (
    id TEXT PRIMARY KEY,
    channel_id TEXT,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_message_at TEXT NOT NULL DEFAULT (datetime('now')),
    message_count INTEGER NOT NULL DEFAULT 0
);

-- Error log for debugging
CREATE TABLE IF NOT EXISTS discord_bot_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    error_message TEXT NOT NULL,
    error_stack TEXT,
    user_message_preview TEXT,
    channel_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_dc_errors_created ON discord_bot_errors(created_at);

-- Channel-to-project routing
CREATE TABLE IF NOT EXISTS discord_channel_projects (
    channel_id TEXT PRIMARY KEY,
    project_id INTEGER REFERENCES projects(id),
    project_name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Dev sessions (shared with Telegram — uses telegram_dev_sessions table
-- with discord_channel_id to distinguish source)

-- Update module version
INSERT OR REPLACE INTO modules (name, version, enabled, installed_at)
VALUES ('discord-bot', '1.0.0', 1, datetime('now'));
