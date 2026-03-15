# Overnight Hub Work — Mar 9, 2026

## What was done

### 1. Task Toggle in ProjectView
**Files:** `hub/src/components/ProjectView.tsx`

Click any task in a project to toggle it between done/todo. Clicking a todo or in_progress task marks it done; clicking a done task marks it todo. The progress bar and task counts update immediately after each toggle.

**How to verify:**
- Open hub → expand Projects → click any project with tasks
- Click a task row — it should toggle status and the progress bar updates
- Click it again to toggle back

---

### 2. Contact Delete
**Files:** `hub/src/components/ContactView.tsx`, `shared/soy_server.py`

Same pattern as project delete: trash icon in the contact header, red confirmation banner with warning text, deletes contact + interactions + follow-ups + generated pages. Linked projects keep their data but lose the client association (via ON DELETE SET NULL).

**How to verify:**
- Open hub → expand People → click a contact
- Trash icon appears top-right next to "Full Brief"
- Click it → confirmation banner appears
- (Don't actually delete a real contact unless you want to — all the FKs cascade correctly)

---

### 3. Sidebar Search
**Files:** `hub/src/components/Sidebar.tsx`

Search bar below the header filters contacts (by name or company) and projects (by name or client name) as you type. Matching sections auto-expand. Non-entity sections (Communications, Intelligence, Tools) hide while searching to reduce noise. Clear button resets. Shows "No matches" when nothing matches.

**Keyboard shortcuts:**
- `Cmd+K` (Mac) / `Ctrl+K` — focus the search
- `Escape` — clear search and blur

**How to verify:**
- Open hub — search bar is visible below "Software of You"
- Type "grow" — should show The Grow App under Projects, and Jessica Martin under People (she's the client)
- Type "zzz" — should show "No matches for zzz"
- Press Cmd+K from anywhere — search focuses
- Press Escape — clears

---

### 4. Live Sidebar Refresh
**Files:** `hub/src/App.tsx`

Sidebar navigation now polls `/api/navigation` every 30 seconds with hash-based diffing (only updates React state when data actually changes). Also refreshes immediately on any navigation event, not just navigate-to-home. This means if you add a contact or project via Claude in another window, the sidebar picks it up within 30 seconds.

**How to verify:**
- Open hub in browser
- In a separate terminal, add a test contact: `sqlite3 ~/.local/share/software-of-you/soy.db "INSERT INTO contacts (name) VALUES ('Test Person');"`
- Wait up to 30 seconds — "Test Person" should appear under People without refreshing
- Clean up: `sqlite3 ~/.local/share/software-of-you/soy.db "DELETE FROM contacts WHERE name = 'Test Person';"`

---

### 5. Hub Build Step in Bootstrap
**Files:** `shared/bootstrap.sh`

Bootstrap now checks if `hub/src` has files newer than `hub/dist/index.html` and rebuilds if so. Skips if dist is up to date. Requires npm to be available (gracefully skips if not). This means any fresh clone or update gets a hub build automatically.

**How to verify:**
- Delete `hub/dist/` and run `bash shared/bootstrap.sh` — hub should rebuild
- Run it again immediately — should skip (dist is current)

---

### 6. Migration: Fix FK Cascades
**Files:** `data/migrations/032_fix_project_fk_cascades.sql`

Fixed three tables that had `project_id REFERENCES projects(id)` without `ON DELETE` clauses (which defaults to RESTRICT, blocking deletes):
- `commitments.linked_project_id` → now `ON DELETE SET NULL`
- `telegram_dev_sessions.project_id` → now `ON DELETE SET NULL`
- `income_records.project_id` → now `ON DELETE SET NULL`

This was the root cause of the "project delete persisting" bug from earlier.

---

### 7. Housekeeping: Task Status Updates

Marked all completed hub tasks as done in the DB. Remaining open task:
- #57: Update /commands/ that generate pages to set parent_page_id

---

## Summary of files changed

| File | Change |
|------|--------|
| `hub/src/App.tsx` | Live nav polling (30s), immediate refresh on navigate |
| `hub/src/components/Sidebar.tsx` | Search bar, Cmd+K shortcut, filtered results |
| `hub/src/components/ProjectView.tsx` | Task toggle click handler, loading state |
| `hub/src/components/ContactView.tsx` | Delete button + confirmation |
| `shared/soy_server.py` | `DELETE /api/contacts/:id` endpoint |
| `shared/bootstrap.sh` | Hub build step |
| `data/migrations/032_fix_project_fk_cascades.sql` | FK constraint fixes |
