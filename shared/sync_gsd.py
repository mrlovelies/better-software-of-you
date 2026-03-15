#!/usr/bin/env python3
"""Read GSD planning artifacts from project workspaces into SoY.

Usage:
    python3 shared/sync_gsd.py read <project_id>     # Parse .planning/ → stdout JSON
    python3 shared/sync_gsd.py sync <project_id>     # Parse .planning/ → store in SoY
    python3 shared/sync_gsd.py status                # List all projects with workspace/GSD info
"""

import json
import os
import re
import sqlite3
import sys

PLUGIN_ROOT = os.environ.get(
    "CLAUDE_PLUGIN_ROOT",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)
DB_PATH = os.path.join(PLUGIN_ROOT, "data", "soy.db")


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _get_project(conn, project_id):
    row = conn.execute(
        "SELECT id, name, workspace_path FROM projects WHERE id = ?",
        (project_id,),
    ).fetchone()
    return dict(row) if row else None


def _parse_roadmap(path):
    """Parse ROADMAP.md — extract phases with completion counts."""
    if not os.path.isfile(path):
        return None

    with open(path, "r") as f:
        content = f.read()

    phases = []
    current_phase = None

    for line in content.split("\n"):
        # Phase headers: ## Phase N: Name or ## Name
        header_match = re.match(r"^##\s+(.+)", line)
        if header_match:
            if current_phase:
                phases.append(current_phase)
            current_phase = {
                "name": header_match.group(1).strip(),
                "total": 0,
                "completed": 0,
            }
            continue

        if current_phase:
            # Completed tasks: - [x] or - [X]
            if re.match(r"^\s*-\s*\[[xX]\]", line):
                current_phase["total"] += 1
                current_phase["completed"] += 1
            # Incomplete tasks: - [ ]
            elif re.match(r"^\s*-\s*\[\s\]", line):
                current_phase["total"] += 1

    if current_phase:
        phases.append(current_phase)

    return {"phases": phases, "raw_length": len(content), "raw": content}


def _parse_state(path):
    """Parse STATE.md — extract current phase, blockers, decisions."""
    if not os.path.isfile(path):
        return None

    with open(path, "r") as f:
        content = f.read()

    result = {"raw": content, "raw_length": len(content)}

    # Try to extract current phase
    phase_match = re.search(
        r"(?:current\s+phase|phase)\s*:\s*(.+)", content, re.IGNORECASE
    )
    if phase_match:
        result["current_phase"] = phase_match.group(1).strip()

    # Try to extract blockers section
    blockers_match = re.search(
        r"##\s*Blockers?\s*\n(.*?)(?=\n##|\Z)", content, re.DOTALL | re.IGNORECASE
    )
    if blockers_match:
        blockers_text = blockers_match.group(1).strip()
        if blockers_text and blockers_text.lower() not in ("none", "n/a", "-"):
            result["blockers"] = blockers_text

    # Try to extract decisions section
    decisions_match = re.search(
        r"##\s*Decisions?\s*\n(.*?)(?=\n##|\Z)", content, re.DOTALL | re.IGNORECASE
    )
    if decisions_match:
        result["decisions"] = decisions_match.group(1).strip()

    return result


def cmd_read(args):
    """Read .planning/ artifacts and output structured JSON."""
    if not args:
        print(json.dumps({"ok": False, "error": "Usage: read <project_id>"}))
        sys.exit(1)

    project_id = int(args[0])
    conn = _get_db()
    project = _get_project(conn, project_id)
    conn.close()

    if not project:
        print(json.dumps({"ok": False, "error": f"Project {project_id} not found"}))
        sys.exit(1)

    workspace = project.get("workspace_path")
    if not workspace or not os.path.isdir(workspace):
        print(json.dumps({"ok": False, "error": "No workspace directory found"}))
        sys.exit(1)

    planning_dir = os.path.join(workspace, ".planning")
    if not os.path.isdir(planning_dir):
        print(json.dumps({
            "ok": True,
            "project_id": project_id,
            "planning_exists": False,
            "message": "No .planning/ directory found",
        }))
        return

    roadmap = _parse_roadmap(os.path.join(planning_dir, "ROADMAP.md"))
    state = _parse_state(os.path.join(planning_dir, "STATE.md"))

    # Strip raw content from output to keep it concise
    result = {
        "ok": True,
        "project_id": project_id,
        "project_name": project["name"],
        "planning_exists": True,
    }

    if roadmap:
        result["roadmap"] = {
            "phases": roadmap["phases"],
            "raw_length": roadmap["raw_length"],
        }

    if state:
        state_summary = {"raw_length": state["raw_length"]}
        if "current_phase" in state:
            state_summary["current_phase"] = state["current_phase"]
        if "blockers" in state:
            state_summary["blockers"] = state["blockers"]
        if "decisions" in state:
            state_summary["decisions"] = state["decisions"]
        result["state"] = state_summary

    print(json.dumps(result))


def cmd_sync(args):
    """Read .planning/ artifacts and store as standalone_notes in SoY."""
    if not args:
        print(json.dumps({"ok": False, "error": "Usage: sync <project_id>"}))
        sys.exit(1)

    project_id = int(args[0])
    conn = _get_db()
    project = _get_project(conn, project_id)

    if not project:
        print(json.dumps({"ok": False, "error": f"Project {project_id} not found"}))
        conn.close()
        sys.exit(1)

    workspace = project.get("workspace_path")
    if not workspace or not os.path.isdir(workspace):
        print(json.dumps({"ok": False, "error": "No workspace directory found"}))
        conn.close()
        sys.exit(1)

    planning_dir = os.path.join(workspace, ".planning")
    if not os.path.isdir(planning_dir):
        print(json.dumps({
            "ok": False,
            "error": "No .planning/ directory found",
        }))
        conn.close()
        sys.exit(1)

    synced = []
    linked_projects = json.dumps([project_id])

    # Sync ROADMAP.md
    roadmap_path = os.path.join(planning_dir, "ROADMAP.md")
    if os.path.isfile(roadmap_path):
        with open(roadmap_path, "r") as f:
            content = f.read()
        title = f"GSD Roadmap: {project['name']}"
        tags = json.dumps(["gsd", "roadmap"])
        _upsert_note(conn, title, content, linked_projects, tags)
        synced.append("ROADMAP.md")

    # Sync STATE.md
    state_path = os.path.join(planning_dir, "STATE.md")
    if os.path.isfile(state_path):
        with open(state_path, "r") as f:
            content = f.read()
        title = f"GSD State: {project['name']}"
        tags = json.dumps(["gsd", "state"])
        _upsert_note(conn, title, content, linked_projects, tags)
        synced.append("STATE.md")

    # Update sync timestamp
    conn.execute(
        """
        INSERT OR REPLACE INTO soy_meta (key, value, updated_at)
        VALUES (?, datetime('now'), datetime('now'))
        """,
        (f"gsd_last_synced_{project_id}",),
    )

    # Log activity
    conn.execute(
        """
        INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
        VALUES ('project', ?, 'gsd_synced', ?, datetime('now'))
        """,
        (project_id, json.dumps({"files_synced": synced})),
    )

    conn.commit()
    conn.close()

    print(json.dumps({"ok": True, "project_id": project_id, "synced": synced}))


def _upsert_note(conn, title, content, linked_projects, tags):
    """Insert or update a standalone_note by title."""
    existing = conn.execute(
        "SELECT id FROM standalone_notes WHERE title = ?", (title,)
    ).fetchone()

    if existing:
        conn.execute(
            """
            UPDATE standalone_notes
            SET content = ?, linked_projects = ?, tags = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (content, linked_projects, tags, existing["id"]),
        )
    else:
        conn.execute(
            """
            INSERT INTO standalone_notes (title, content, linked_projects, tags, created_at, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            (title, content, linked_projects, tags),
        )


def cmd_status(args):
    """List all projects with workspace and GSD info."""
    conn = _get_db()
    rows = conn.execute(
        "SELECT id, name, workspace_path, status FROM projects ORDER BY id"
    ).fetchall()

    projects = []
    for r in rows:
        info = {
            "project_id": r["id"],
            "name": r["name"],
            "status": r["status"],
            "workspace_path": r["workspace_path"],
        }

        wp = r["workspace_path"]
        if wp and os.path.isdir(wp):
            info["dir_exists"] = True
            info["gsd_installed"] = os.path.isdir(
                os.path.join(wp, ".claude", "commands", "gsd")
            )
            info["has_planning"] = os.path.isdir(os.path.join(wp, ".planning"))
        else:
            info["dir_exists"] = False if wp else None

        # Check last sync time
        meta_key = f"gsd_last_synced_{r['id']}"
        meta = conn.execute(
            "SELECT value FROM soy_meta WHERE key = ?", (meta_key,)
        ).fetchone()
        info["last_gsd_sync"] = meta["value"] if meta else None

        projects.append(info)

    conn.close()
    print(json.dumps({"ok": True, "projects": projects}))


def main():
    if len(sys.argv) < 2:
        print("Usage: sync_gsd.py <read|sync|status> [project_id]")
        sys.exit(1)

    command = sys.argv[1]
    rest = sys.argv[2:]

    commands = {
        "read": cmd_read,
        "sync": cmd_sync,
        "status": cmd_status,
    }

    if command not in commands:
        print(json.dumps({"ok": False, "error": f"Unknown command: {command}"}))
        sys.exit(1)

    commands[command](rest)


if __name__ == "__main__":
    main()
