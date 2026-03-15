---
description: Persist session context for cross-interface continuity
allowed-tools: ["Bash", "Read", "Write"]
---

# Handoff — Session Continuation Prompt

Generate a structured handoff document for continuing this work in ANY SoY interface (new Claude Code session, Telegram, Hub).

## Steps

1. **Summarize accomplishments.** List what was completed this session — features added, bugs fixed, configs changed.
2. **List remaining work.** What's unfinished or blocked? Be specific about what's left.
3. **Key file paths.** Every file that was created or modified, grouped by purpose.
4. **Gotchas discovered.** Anything surprising — env quirks, schema oddities, API behaviors, things that didn't work as expected.
5. **Current state.** Branch name, uncommitted changes, running processes, any temp state.

## Output Format

Produce a single copyable markdown block the user can paste into a new session:

```
## Session Handoff — [date]

### Done
- ...

### Remaining
- ...

### Key Files
- ...

### Gotchas
- ...

### State
- Branch: ...
- Uncommitted: yes/no
- Running processes: ...
```

## Persistence (MANDATORY)

After generating the handoff, you MUST do both of these:

### 1. Write to file
Create the tasks directory if needed, then write the handoff:
```bash
mkdir -p "${CLAUDE_PLUGIN_ROOT:-$(pwd)}/tasks"
```
Write the handoff to `tasks/handoff.md` so `/pickup` can find it in a new Claude Code session.

### 2. Write to database
Expire any previous active handoffs, then insert the new one with the source machine hostname:

```bash
sqlite3 "${CLAUDE_PLUGIN_ROOT:-$(pwd)}/data/soy.db" <<'SQL'
UPDATE session_handoffs SET status = 'expired' WHERE status = 'active';
SQL

sqlite3 "${CLAUDE_PLUGIN_ROOT:-$(pwd)}/data/soy.db" <<SQL
INSERT INTO session_handoffs (summary, project_ids, branch, source, status)
VALUES (
  '$(cat tasks/handoff.md | sed "s/'/''/g")',
  NULL,
  '$(git branch --show-current 2>/dev/null || echo "unknown")',
  'claude-code@$(hostname -s 2>/dev/null || echo "unknown")',
  'active'
);
SQL
```

If you know which project IDs were touched, set `project_ids` to a JSON array like `'[1, 209]'`.

This is what makes Telegram and other interfaces aware of your session. Without the DB write, the handoff is invisible outside Claude Code.
