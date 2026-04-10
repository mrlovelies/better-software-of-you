import Database from 'better-sqlite3';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const PLUGIN_ROOT = process.env.CLAUDE_PLUGIN_ROOT || join(__dirname, '../..');
const DB_PATH = join(PLUGIN_ROOT, 'data', 'soy.db');

// Dashboard-specific tables (users, invites, sessions)
const DASHBOARD_MIGRATION = `
CREATE TABLE IF NOT EXISTS dashboard_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    name TEXT,
    picture TEXT,
    google_id TEXT UNIQUE,
    role TEXT NOT NULL DEFAULT 'viewer',  -- 'admin', 'reviewer', 'viewer'
    invited_by INTEGER REFERENCES dashboard_users(id),
    created_at TEXT DEFAULT (datetime('now')),
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS dashboard_invites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'reviewer',
    token TEXT NOT NULL UNIQUE,
    invited_by INTEGER NOT NULL REFERENCES dashboard_users(id),
    accepted_at TEXT,
    expires_at TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS dashboard_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES dashboard_users(id),
    token TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
`;

export function createDb() {
  const db = new Database(DB_PATH);
  db.pragma('journal_mode = WAL');
  db.pragma('busy_timeout = 5000');
  db.exec(DASHBOARD_MIGRATION);
  return db;
}
