# /launch — Project Session Launcher

Launch a Claude instance for a project in its own tmux window.

## Usage

- `/launch` or `/launch list` — show active project sessions
- `/launch ensure-tmux` — check tmux setup and guide if needed
- `/launch <project name or ID>` — launch Claude in a project workspace
- `/launch stop <name>` — stop a project session

## Implementation

### Step 1: Parse the argument

If no argument or argument is "list":
```bash
RESULT=$(python3 "${CLAUDE_PLUGIN_ROOT:-$(pwd)}/shared/launch_project.py" list)
```

If argument is "ensure-tmux":
```bash
RESULT=$(python3 "${CLAUDE_PLUGIN_ROOT:-$(pwd)}/shared/launch_project.py" ensure-tmux)
```

If argument starts with "stop":
```bash
RESULT=$(python3 "${CLAUDE_PLUGIN_ROOT:-$(pwd)}/shared/launch_project.py" stop <rest_of_args>)
```

Otherwise, treat the argument as a project to launch:
```bash
RESULT=$(python3 "${CLAUDE_PLUGIN_ROOT:-$(pwd)}/shared/launch_project.py" launch <args>)
```

### Step 2: Parse and present the JSON result

**ensure-tmux results:**
- If `in_tmux` is true: "You're inside tmux. Ready to launch projects."
- If `in_tmux` is false and `session_exists` is true: "A `soy` tmux session exists. Run this to attach:\n`<command>`"
- If `in_tmux` is false and `session_exists` is false: "No tmux session yet. Run this to start one:\n`<command>`\nThen re-run `/launch` from inside tmux."
- If error with hint "brew install tmux": "tmux isn't installed. Run `brew install tmux` first."

**launch results:**
- If `already_running` is true: "**<project_name>** is already running in window <window_index>. Switch with `Ctrl-b` then `<window_index>`, or:\n`<switch_command>`"
- If successful: "Launched Claude in **<project_name>** — switch with `Ctrl-b` then the window number, or:\n`<switch_command>`"
- If error with hint about `/project-init`: "**<project_name>** doesn't have a workspace yet. Run `/project-init <project_id>` to set one up."
- If error "Not inside a tmux session": Guide user to run ensure-tmux first.
- If error "Multiple projects match": Show the matches as a table and ask user to be more specific or use the project ID.

**list results:**
- If no windows: "No active project sessions."
- If windows exist: Show a table with columns: Window #, Name, Project, Command, Active. Cross-reference `project` field from the result for project names. Highlight the active window.

**stop results:**
- If successful: "Stopped **<name>**."
- If error: Show the error message. If `available` is present, list available window names.
