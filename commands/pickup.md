---
description: Resume from a previous session handoff
allowed-tools: ["Bash", "Read"]
---

# Pickup — Resume from Handoff

Pick up where the last session left off by reading the handoff document.

## Steps

1. **Find the handoff.** Check both sources:
   - `tasks/handoff.md` in the current project root (file-based)
   - `session_handoffs` table in the DB:
     ```sql
     SELECT summary, source, datetime(created_at, 'localtime') as created_at
     FROM session_handoffs WHERE status = 'active'
     ORDER BY created_at DESC LIMIT 1
     ```
   - Use whichever is more recent. If neither exists, tell the user there's no handoff to pick up and suggest running `/handoff` at the end of their next session.

2. **Present the context.** Display the handoff contents so the user can see what was done, what's remaining, and the current state. If the handoff has a `source` field, show which machine it came from (e.g. "Handed off from claude-code@razerblade").

3. **Check git state.** Run `git status` and `git branch` to confirm the branch and working tree match what the handoff describes. Flag any discrepancies.

4. **Suggest next steps.** Based on the "Remaining" section, propose what to tackle first.

5. **Mark as picked up.** Update the DB record:
   ```sql
   UPDATE session_handoffs
   SET status = 'picked_up',
       picked_up_at = datetime('now'),
       picked_up_by = 'claude-code@$(hostname -s 2>/dev/null || echo "unknown")'
   WHERE status = 'active';
   ```

6. **Clean up.** Delete `tasks/handoff.md` so it doesn't go stale.
