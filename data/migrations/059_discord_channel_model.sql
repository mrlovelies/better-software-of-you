-- Add per-channel model preference to Discord channel routing.
-- Allows channels like #deep-work to use Opus while others stay on Sonnet.

ALTER TABLE discord_channel_projects ADD COLUMN preferred_model TEXT DEFAULT NULL;

-- Update module version
INSERT OR REPLACE INTO modules (name, version, enabled, installed_at)
VALUES ('discord-bot', '1.1.0', 1, datetime('now'));
