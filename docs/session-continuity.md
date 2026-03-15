# Session Continuity

Cross-interface handoffs for Software of You. When you end a Claude Code session, the context is persisted so Telegram, a new terminal, or another machine can pick up where you left off.

## How It Works

1. **You use `cc` instead of `claude`** — a thin shell wrapper that runs Claude Code normally
2. **On exit**, `cc` continues the session non-interactively to run `/handoff`
3. **The handoff** writes session context to both `tasks/handoff.md` (file) and `session_handoffs` (DB)
4. **Other interfaces** (Telegram bot, new Claude Code sessions) check for active handoffs
5. **On pickup**, the handoff is marked as consumed and the file is cleaned up

## Setup

Run `/session-setup` from Claude Code on each machine. This:
- Makes `shared/cc` executable
- Symlinks it to `~/bin/cc`
- Checks your PATH

After setup, use `cc` instead of `claude` to start sessions.

## Usage

### Starting a session
```bash
cc                    # interactive, with auto-handoff on exit
cc -p "question"      # pipe mode, no handoff (one-shot queries)
```

If an active handoff exists, `cc` shows a one-line reminder on startup:
```
↳ Active handoff from claude-code@macbook · 2026-03-14 15:30:00 — run /pickup to resume
```

### Ending a session
Just exit normally (`/exit`, Ctrl+C, Ctrl+D). The wrapper handles the rest.

### Picking up
In any new session:
```
/pickup
```
This reads the handoff, shows what was done and what's left, checks git state for discrepancies, and marks the handoff as consumed.

### Manual handoff
If you didn't use `cc`, or want to handoff mid-session:
```
/handoff
```

## Architecture

### Storage
- **File**: `tasks/handoff.md` — for Claude Code sessions (git-ignored)
- **Database**: `session_handoffs` table — for Telegram and cross-machine access

### Source Tracking
The `source` field includes the hostname: `claude-code@macbook`, `telegram`, etc. This lets you see which machine a handoff came from when picking up on a different one.

### Schema
```sql
CREATE TABLE session_handoffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    summary TEXT NOT NULL,
    project_ids TEXT,           -- JSON array of project IDs
    branch TEXT,
    source TEXT NOT NULL,       -- e.g. 'claude-code@macbook'
    status TEXT NOT NULL        -- 'active', 'picked_up', 'expired'
        CHECK (status IN ('active', 'picked_up', 'expired')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    picked_up_at TEXT,
    picked_up_by TEXT
);
```

### Cross-Machine Flow
```
MacBook (cc)                    Razer (cc or Telegram)
  │                               │
  ├─ work session                 │
  ├─ exit → auto /handoff         │
  │   ├─ tasks/handoff.md         │
  │   └─ DB: session_handoffs     │
  │         (synced via Syncthing) │
  │                               ├─ cc startup: "Active handoff..."
  │                               ├─ /pickup
  │                               │   ├─ reads handoff
  │                               │   ├─ shows source machine
  │                               │   └─ marks picked_up
  │                               ├─ continue work
  │                               └─ exit → auto /handoff
```

## Files
- `shared/cc` — Shell wrapper
- `commands/handoff.md` — Handoff command definition
- `commands/pickup.md` — Pickup command definition
- `commands/session-setup.md` — Per-machine setup command
