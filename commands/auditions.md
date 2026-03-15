---
description: View and manage your audition pipeline
allowed-tools: ["Bash", "Read"]
argument-hint: [list|new|update <id>|board]
---

# Auditions Pipeline

Manage auditions from casting platforms and manual entries. Database at `${CLAUDE_PLUGIN_ROOT:-$(pwd)}/data/soy.db`.

## Determine Action

Parse $ARGUMENTS:
- **No arguments** or **"list"** → Step 1 (List)
- **"new"** → Step 2 (Add new)
- **"update <id>"** → Step 3 (Update)
- **"board"** → Step 4 (Generate board)
- **"scan"** → Step 5 (Scan emails)

---

## Step 1: List Active Auditions

Query active auditions from the pipeline view:
```sql
SELECT id, project_name, role_name, role_type, production_type, source, status,
       casting_director, received_at, deadline, urgency, days_until_deadline,
       days_since_received, agent_name
FROM v_audition_pipeline
WHERE status NOT IN ('passed', 'expired')
ORDER BY
    CASE status
        WHEN 'new' THEN 1
        WHEN 'reviewing' THEN 2
        WHEN 'preparing' THEN 3
        WHEN 'recorded' THEN 4
        WHEN 'submitted' THEN 5
        WHEN 'callback' THEN 6
        WHEN 'booked' THEN 7
    END,
    COALESCE(deadline, '9999-12-31') ASC;
```

**Display format:**
- Group by status (New, Reviewing, Preparing, Recorded, Submitted, Callback, Booked)
- For each audition show: project name, role, source badge, casting director, deadline
- Urgency indicators: 🔴 for urgent (< 1 day), 🟡 for soon (< 3 days)
- Show days since received

If no active auditions: "No active auditions. Use `/auditions new` to add one, or `/auditions scan` to check your email."

Also show a brief count of passed/expired:
```sql
SELECT status, COUNT(*) FROM auditions WHERE status IN ('passed', 'expired') GROUP BY status;
```

---

## Step 2: Add New Audition

Ask the user for audition details using `AskUserQuestion`:

1. Ask: "What's the project name?" (free text — just ask conversationally, don't use AskUserQuestion for this)
2. Ask role name conversationally
3. Use `AskUserQuestion` with:
   - **Q1** (header: "Source"): "Where did this come from?" — options: Casting Workbook, Actors Access, WeAudition, Backstage, Manual/Other (multiSelect: false)
   - **Q2** (header: "Type"): "What kind of production?" — options: TV Series, Film, Commercial, Audiobook, Theatre (multiSelect: false)
4. Ask for deadline conversationally (can be "none" or a date)
5. Ask for casting director name (optional)

Insert the audition:
```sql
INSERT INTO auditions (project_name, role_name, source, production_type, casting_director, deadline, agent_contact_id, status, received_at)
VALUES (?, ?, ?, ?, ?, ?, CASE WHEN source IN ('castingworkbook','actorsaccess','weaudition') THEN 2 ELSE NULL END, 'new', datetime('now'));
```

Log activity:
```sql
INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
VALUES ('audition', <id>, 'created', 'Manual audition entry', datetime('now'));
```

Confirm: "Added **[project]** — [role] to your pipeline."

---

## Step 3: Update Audition

Extract the audition ID from $ARGUMENTS. Fetch current state:
```sql
SELECT * FROM v_audition_pipeline WHERE id = <id>;
```

Show current details, then ask what to update using `AskUserQuestion`:
- **Q1** (header: "Update"): "What would you like to update?" — options: Status, Add notes, Set deadline, Mark submitted (multiSelect: false)

**If Status:** Ask for new status:
- Options: Reviewing, Preparing, Recorded, Submitted, Callback, Booked, Passed
- Update: `UPDATE auditions SET status = ?, updated_at = datetime('now') WHERE id = ?;`
- If status = 'submitted', also set `submitted_at = datetime('now')`

**If Add notes:** Ask for notes text, then:
- `UPDATE auditions SET notes = COALESCE(notes || char(10), '') || ?, updated_at = datetime('now') WHERE id = ?;`

**If Set deadline:** Ask for date, then:
- `UPDATE auditions SET deadline = ?, updated_at = datetime('now') WHERE id = ?;`

**If Mark submitted:** Set status and timestamp:
- `UPDATE auditions SET status = 'submitted', submitted_at = datetime('now'), updated_at = datetime('now') WHERE id = ?;`

Log activity and confirm.

---

## Step 4: Generate Board

Delegate to the `/audition-board` skill:
- Use the Skill tool to invoke "audition-board"

---

## Step 5: Scan Emails

Run the audition email scanner:
```
python3 "${CLAUDE_PLUGIN_ROOT:-$(pwd)}/shared/sync_auditions.py" scan
```

Parse JSON output. Present results:
- **If imported > 0:** "Found **N new auditions** from your casting emails:" — list each with project, role, source
- **If imported == 0:** "No new casting emails found."
- **If errors:** Mention briefly

Then show the full active list (Step 1).
