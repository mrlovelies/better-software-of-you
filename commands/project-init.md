---
description: Bootstrap a project workspace with git, GSD, and a SoY-managed CLAUDE.md
allowed-tools: ["Bash", "Read"]
argument-hint: <project name or id> [--path /abs/path] [--skip-gsd]
---

# Project Init

Bootstrap a workspace for the project specified in `$ARGUMENTS`. Creates the directory, initializes git, installs GSD (Get Shit Done), and generates a CLAUDE.md with live project + client data from SoY.

## Step 1: Resolve Project

Query `${CLAUDE_PLUGIN_ROOT:-$(pwd)}/data/soy.db` to find the project:

```sql
SELECT p.id, p.name, p.workspace_path, p.status,
       c.name AS client_name
FROM projects p
LEFT JOIN contacts c ON p.client_id = c.id
WHERE p.name LIKE '%$ARGUMENTS%' OR p.id = CAST('$ARGUMENTS' AS INTEGER);
```

Strip any flags (`--path`, `--skip-gsd`) from `$ARGUMENTS` before matching.

If no match found, ask the user if they want to create a new project first (use `/project` to create it, then come back to `/project-init`).

If multiple matches, pick the best one or ask.

## Step 2: Check Existing Workspace

If the project already has a `workspace_path` set and the directory exists:
- Tell the user: "This project already has a workspace at `<path>`."
- Offer to run `refresh` instead to update the CLAUDE.md with fresh data.
- If they want to re-init, they can pass `--path` to set a new location.

## Step 3: Run Bootstrap

Extract flags from `$ARGUMENTS`:
- `--path /some/path` → pass through to init_project.py
- `--skip-gsd` → pass through to init_project.py

Run:
```bash
python3 "${CLAUDE_PLUGIN_ROOT:-$(pwd)}/shared/init_project.py" init <project_id> [flags]
```

Parse the JSON output.

## Step 4: Report Results

Based on the JSON response, tell the user what happened:

- **Workspace created at:** `<path>`
- **Git initialized:** yes/already existed
- **GSD installed:** yes/skipped/npx not available
- **CLAUDE.md written:** yes/skipped (user-customized)
- **PROJECT.md seeded:** yes/no

## Step 5: Suggest Next Step

Pick ONE suggestion based on what happened:

- If GSD was installed → "Open the workspace and run `/gsd:new-project` to kick off your first build phase."
- If GSD was skipped because npx isn't available → "GSD was skipped — install Node.js or run `npx get-shit-done-cc --claude --local --auto` in the workspace manually."
- If GSD was skipped by flag → "When you're ready for structured planning, run `/project-init <name>` again without `--skip-gsd`."
- If workspace already existed → "Your workspace is ready. Open it in Claude Code to start working."
