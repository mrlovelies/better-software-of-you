---
description: Launch the interactive audition pipeline board
allowed-tools: ["Bash", "Read", "Write"]
---

# Audition Board — Interactive Kanban

Launch the live audition board with drag-and-drop and inline editing. Database at `${CLAUDE_PLUGIN_ROOT:-$(pwd)}/data/soy.db`.

## Step 1: Open the Audition Board

```bash
bash "${CLAUDE_PLUGIN_ROOT:-$(pwd)}/shared/open_page.sh" audition-board.html
```

This auto-starts the server if needed and opens the auditions route.

## Step 2: Confirm

Tell the user:

"Audition board is live at **http://localhost:8787/auditions**

- **Drag** cards between columns to change status
- **Click** any card to edit details (role, deadline, notes, casting director, etc.)
- **+ Add** button to create a new audition
- Changes save instantly to your database
- **Home** link in sidebar takes you to the SoY Hub

The server runs in the background. It'll stop automatically when you close this session, or you can stop it with `pkill -f soy_server`."
