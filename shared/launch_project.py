#!/usr/bin/env python3
"""tmux session/window management for project-specific Claude instances.

Usage:
    python3 shared/launch_project.py ensure-tmux
    python3 shared/launch_project.py launch <project_id_or_name>
    python3 shared/launch_project.py list
    python3 shared/launch_project.py stop <name>
"""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys

PLUGIN_ROOT = os.environ.get(
    "CLAUDE_PLUGIN_ROOT",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)
DB_PATH = os.path.join(PLUGIN_ROOT, "data", "soy.db")
SESSION_NAME = "soy"


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _slugify(name):
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"[\s]+", "-", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-")


def _tmux_installed():
    return shutil.which("tmux") is not None


def _in_tmux():
    return bool(os.environ.get("TMUX"))


def _session_exists(name):
    result = subprocess.run(
        ["tmux", "has-session", "-t", name],
        capture_output=True,
    )
    return result.returncode == 0


def _list_windows(session):
    result = subprocess.run(
        ["tmux", "list-windows", "-t", session,
         "-F", "#{window_index}|#{window_name}|#{pane_current_command}|#{window_active}"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return []
    windows = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("|")
        if len(parts) >= 4:
            windows.append({
                "index": parts[0],
                "name": parts[1],
                "command": parts[2],
                "active": parts[3] == "1",
            })
    return windows


def _resolve_project(conn, query):
    """Resolve project by ID or fuzzy name match."""
    # Try as integer ID first
    try:
        pid = int(query)
        row = conn.execute(
            "SELECT id, name, workspace_path, status FROM projects WHERE id = ?",
            (pid,),
        ).fetchone()
        if row:
            return dict(row)
    except ValueError:
        pass

    # Fuzzy name match
    q = f"%{query}%"
    rows = conn.execute(
        "SELECT id, name, workspace_path, status FROM projects WHERE LOWER(name) LIKE LOWER(?)",
        (q,),
    ).fetchall()

    if len(rows) == 1:
        return dict(rows[0])
    elif len(rows) > 1:
        matches = [{"id": r["id"], "name": r["name"]} for r in rows]
        return {"ambiguous": True, "matches": matches}
    return None


# --- Subcommands ---


def cmd_ensure_tmux():
    if not _tmux_installed():
        print(json.dumps({
            "ok": False,
            "error": "tmux is not installed",
            "hint": "Run: brew install tmux",
        }))
        sys.exit(1)

    if _in_tmux():
        print(json.dumps({
            "ok": True,
            "in_tmux": True,
            "session_exists": _session_exists(SESSION_NAME),
            "message": "Already inside tmux.",
        }))
        return

    if _session_exists(SESSION_NAME):
        print(json.dumps({
            "ok": True,
            "in_tmux": False,
            "session_exists": True,
            "command": f"tmux attach -t {SESSION_NAME}",
            "message": f"Session '{SESSION_NAME}' exists. Attach to it.",
        }))
    else:
        print(json.dumps({
            "ok": True,
            "in_tmux": False,
            "session_exists": False,
            "command": f"tmux new-session -s {SESSION_NAME}",
            "message": f"No tmux session. Start one.",
        }))


def cmd_launch(args):
    if not args:
        print(json.dumps({"ok": False, "error": "Usage: launch <project_id_or_name>"}))
        sys.exit(1)

    query = " ".join(args)

    if not _tmux_installed():
        print(json.dumps({
            "ok": False,
            "error": "tmux is not installed",
            "hint": "Run: brew install tmux",
        }))
        sys.exit(1)

    if not _in_tmux():
        print(json.dumps({
            "ok": False,
            "error": "Not inside a tmux session",
            "hint": f"Run: tmux new-session -s {SESSION_NAME}",
        }))
        sys.exit(1)

    conn = _get_db()
    project = _resolve_project(conn, query)

    if project is None:
        print(json.dumps({"ok": False, "error": f"No project found matching '{query}'"}))
        conn.close()
        sys.exit(1)

    if project.get("ambiguous"):
        print(json.dumps({
            "ok": False,
            "error": "Multiple projects match",
            "matches": project["matches"],
        }))
        conn.close()
        sys.exit(1)

    workspace = project.get("workspace_path")
    if not workspace or not os.path.isdir(workspace):
        print(json.dumps({
            "ok": False,
            "error": f"No workspace directory for '{project['name']}'",
            "hint": "Run /project-init to set up the workspace first.",
        }))
        conn.close()
        sys.exit(1)

    slug = _slugify(project["name"])

    # Check if window already exists
    if _session_exists(SESSION_NAME):
        windows = _list_windows(SESSION_NAME)
        for w in windows:
            if w["name"] == slug:
                print(json.dumps({
                    "ok": True,
                    "already_running": True,
                    "window_name": slug,
                    "window_index": w["index"],
                    "switch_command": f"tmux select-window -t {SESSION_NAME}:{slug}",
                    "message": f"'{project['name']}' is already running in window {w['index']}.",
                }))
                conn.close()
                return

    # Create session if it doesn't exist
    if not _session_exists(SESSION_NAME):
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", SESSION_NAME],
            capture_output=True,
        )

    # Create window
    subprocess.run(
        ["tmux", "new-window", "-t", SESSION_NAME, "-n", slug, "-c", workspace],
        capture_output=True,
    )

    # Send claude command
    subprocess.run(
        ["tmux", "send-keys", "-t", f"{SESSION_NAME}:{slug}", "claude", "Enter"],
        capture_output=True,
    )

    # Log activity
    conn.execute(
        """INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
           VALUES ('project', ?, 'tmux_session_launched', ?, datetime('now'))""",
        (project["id"], json.dumps({"window": slug, "workspace": workspace})),
    )
    conn.commit()
    conn.close()

    print(json.dumps({
        "ok": True,
        "window_name": slug,
        "workspace": workspace,
        "project_name": project["name"],
        "switch_command": f"tmux select-window -t {SESSION_NAME}:{slug}",
        "shortcut": "Ctrl-b then window number",
        "message": f"Launched Claude in '{project['name']}'.",
    }))


def cmd_list():
    if not _tmux_installed():
        print(json.dumps({"ok": False, "error": "tmux is not installed"}))
        sys.exit(1)

    if not _session_exists(SESSION_NAME):
        print(json.dumps({"ok": True, "windows": [], "message": "No soy tmux session running."}))
        return

    windows = _list_windows(SESSION_NAME)

    # Cross-reference with projects
    conn = _get_db()
    projects = conn.execute("SELECT id, name, workspace_path FROM projects").fetchall()
    slug_map = {}
    for p in projects:
        slug_map[_slugify(p["name"])] = {"id": p["id"], "name": p["name"], "workspace": p["workspace_path"]}
    conn.close()

    enriched = []
    for w in windows:
        entry = {**w}
        if w["name"] in slug_map:
            entry["project"] = slug_map[w["name"]]
        enriched.append(entry)

    print(json.dumps({"ok": True, "windows": enriched}))


def cmd_stop(args):
    if not args:
        print(json.dumps({"ok": False, "error": "Usage: stop <window_name>"}))
        sys.exit(1)

    name = _slugify(" ".join(args))

    if not _tmux_installed() or not _session_exists(SESSION_NAME):
        print(json.dumps({"ok": False, "error": "No soy tmux session running."}))
        sys.exit(1)

    # Check window exists
    windows = _list_windows(SESSION_NAME)
    found = any(w["name"] == name for w in windows)
    if not found:
        print(json.dumps({
            "ok": False,
            "error": f"No window named '{name}'",
            "available": [w["name"] for w in windows],
        }))
        sys.exit(1)

    subprocess.run(
        ["tmux", "kill-window", "-t", f"{SESSION_NAME}:{name}"],
        capture_output=True,
    )

    print(json.dumps({"ok": True, "stopped": name, "message": f"Window '{name}' stopped."}))


def main():
    if len(sys.argv) < 2:
        print("Usage: launch_project.py <ensure-tmux|launch|list|stop> [args...]")
        sys.exit(1)

    command = sys.argv[1]
    rest = sys.argv[2:]

    commands = {
        "ensure-tmux": lambda: cmd_ensure_tmux(),
        "launch": lambda: cmd_launch(rest),
        "list": lambda: cmd_list(),
        "stop": lambda: cmd_stop(rest),
    }

    if command not in commands:
        print(json.dumps({"ok": False, "error": f"Unknown command: {command}"}))
        sys.exit(1)

    commands[command]()


if __name__ == "__main__":
    main()
