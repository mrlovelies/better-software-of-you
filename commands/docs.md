---
description: Create, read, edit, and manage Google Docs
allowed-tools: ["Bash", "Read", "Write"]
argument-hint: [create <title> | read <id> | edit <id> | list | search <query> | export <title>]
---

# Google Docs

Create, read, edit, list, and export Google Docs. Documents are tracked locally and can be linked to contacts and projects.

## Step 1: Check Authentication

Get a valid access token:
```
ACCESS_TOKEN=$(python3 "${CLAUDE_PLUGIN_ROOT:-$(pwd)}/shared/google_auth.py" token)
```

If this fails, tell the user: "Google isn't connected yet. Run `/google-setup` to connect your Google account."

If the token exists but Docs operations fail with a 403, the user may need to re-authorize with the new scopes:
```
python3 "${CLAUDE_PLUGIN_ROOT:-$(pwd)}/shared/google_auth.py" auth
```

## Step 2: Determine the Operation

Parse $ARGUMENTS:

- **"create <title>"** → Create a new Google Doc with the given title
- **"read <id>"** → Read a document's content (Google Doc ID or local numeric ID)
- **"edit <id>"** → Replace a document's content (prompts for content)
- **"list"** → List all tracked documents
- **"search <query>"** → Search tracked docs by title or content
- **"link <id>"** → Link a doc to a contact or project
- **"export <title>"** → Create a new doc from content (provide title, then content)
- **No arguments** → List recent documents

## Step 3: Execute via MCP Tool or API

**Preferred:** Use the `docs` MCP tool if available — it handles auth, tracking, and cross-referencing automatically.

**Fallback (direct API):** If the MCP tool is not available, use curl:

**Create a document:**
```bash
curl -s -X POST \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title": "My Document"}' \
  "https://docs.googleapis.com/v1/documents"
```

**Read a document:**
```bash
curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
  "https://docs.googleapis.com/v1/documents/DOCUMENT_ID"
```

**Insert text into a document:**
```bash
curl -s -X POST \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"requests": [{"insertText": {"location": {"index": 1}, "text": "Hello world"}}]}' \
  "https://docs.googleapis.com/v1/documents/DOCUMENT_ID:batchUpdate"
```

## Step 4: Track in Database

After any create/read operation, ensure the doc is tracked locally:
```sql
INSERT OR REPLACE INTO google_docs (google_doc_id, title, url, last_synced_at, updated_at)
VALUES ('<doc_id>', '<title>', 'https://docs.google.com/document/d/<doc_id>/edit', datetime('now'), datetime('now'));
```

## Step 5: Cross-Reference

- If the user mentions a contact or project when creating/linking a doc, set `contact_id` or `project_id`
- When listing docs for a contact/project, filter by those foreign keys
- Log all operations to `activity_log` with `entity_type = 'google_doc'`

## Step 6: Present Results

- Show the document title and URL
- For reads, show content with reasonable truncation (first ~2000 chars)
- For lists, show as a table: Title | Type | Linked To | Last Updated
- Always include the Google Docs URL so the user can open it in a browser
