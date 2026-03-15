#!/usr/bin/env python3
"""Bootstrap a project workspace with git, GSD, and a SoY-managed CLAUDE.md.

Usage:
    python3 shared/init_project.py init <project_id> [--path /abs/path] [--skip-gsd]
    python3 shared/init_project.py status <project_id>
    python3 shared/init_project.py refresh <project_id>
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
TEMPLATE_PATH = os.path.join(
    PLUGIN_ROOT, "mcp-server", "templates", "project-claude-md.template"
)
DEFAULT_WORKSPACE_ROOT = os.path.expanduser(
    os.environ.get("SOY_WORKSPACE_ROOT", "~/wkspaces")
)

SOY_MANAGED_SENTINEL = "<!-- SOY-MANAGED -->"


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _slugify(name):
    """Lowercase, strip non-alphanumeric (except hyphens/spaces), replace spaces with hyphens."""
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"[\s]+", "-", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-")


def _get_project(conn, project_id):
    """Fetch project with client info."""
    row = conn.execute(
        """
        SELECT p.*, c.name AS client_name, c.email AS client_email,
               c.company AS client_company, c.role AS client_role
        FROM projects p
        LEFT JOIN contacts c ON p.client_id = c.id
        WHERE p.id = ?
        """,
        (project_id,),
    ).fetchone()
    if not row:
        return None
    return dict(row)


def _get_user_profile(conn):
    """Fetch user profile fields."""
    rows = conn.execute(
        "SELECT category, key, value FROM user_profile WHERE category IN ('identity', 'preferences')"
    ).fetchall()
    profile = {}
    for r in rows:
        profile[f"{r['category']}.{r['key']}"] = r["value"]
    return profile


def _get_recent_interactions(conn, contact_id, limit=3):
    """Fetch last N interactions for a contact."""
    if not contact_id:
        return []
    rows = conn.execute(
        """
        SELECT type, direction, subject, summary, occurred_at
        FROM contact_interactions
        WHERE contact_id = ?
        ORDER BY occurred_at DESC
        LIMIT ?
        """,
        (contact_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def _render_template(project, profile, interactions):
    """Render the CLAUDE.md template with project data."""
    with open(TEMPLATE_PATH, "r") as f:
        template = f.read()

    # Build substitution values
    client_company = ""
    if project.get("client_company"):
        client_company = f" ({project['client_company']})"

    client_details = ""
    if project.get("client_email"):
        client_details += f"\n- Email: {project['client_email']}"
    if project.get("client_role"):
        client_details += f"\n- Role: {project['client_role']}"

    # Format interactions
    if interactions:
        interaction_lines = []
        for ix in interactions:
            date = ix.get("occurred_at", "—")[:10]
            kind = ix.get("type", "other")
            direction = ix.get("direction", "")
            subject = ix.get("subject") or ""
            summary = ix.get("summary") or "No summary"
            line = f"- **{date}** — {kind} ({direction})"
            if subject:
                line += f": {subject}"
            line += f"\n  {summary}"
            interaction_lines.append(line)
        recent_interactions = "\n".join(interaction_lines)
    else:
        recent_interactions = "_No interactions recorded yet._"

    replacements = {
        "{PROJECT_NAME}": project.get("name", "Untitled Project"),
        "{PROJECT_DESCRIPTION}": project.get("description") or "_No description set._",
        "{PROJECT_ID}": str(project.get("id", "")),
        "{PROJECT_STATUS}": project.get("status", "active"),
        "{PROJECT_START_DATE}": project.get("start_date") or "—",
        "{PROJECT_TARGET_DATE}": project.get("target_date") or "—",
        "{PROJECT_PRIORITY}": project.get("priority") or "medium",
        "{CLIENT_NAME}": project.get("client_name") or "—",
        "{CLIENT_COMPANY}": client_company,
        "{CLIENT_DETAILS}": client_details,
        "{RECENT_INTERACTIONS}": recent_interactions,
        "{DEV_NAME}": profile.get("identity.name", "—"),
        "{DEV_ROLE}": profile.get("identity.role", "—"),
        "{DEV_COMM_STYLE}": profile.get("preferences.communication_style", "—"),
    }

    result = template
    for token, value in replacements.items():
        result = result.replace(token, value)

    return result


def _has_gsd(workspace):
    """Check if GSD commands are installed."""
    return os.path.isdir(os.path.join(workspace, ".claude", "commands", "gsd"))


def _has_planning(workspace):
    """Check if .planning/ directory exists."""
    return os.path.isdir(os.path.join(workspace, ".planning"))


def _claude_md_is_soy_managed(workspace):
    """Check if CLAUDE.md exists and contains the SOY-MANAGED sentinel."""
    claude_md = os.path.join(workspace, "CLAUDE.md")
    if not os.path.isfile(claude_md):
        return None  # doesn't exist
    with open(claude_md, "r") as f:
        content = f.read()
    return SOY_MANAGED_SENTINEL in content


# --- Subcommands ---


def cmd_init(args):
    """Bootstrap a project workspace."""
    if not args:
        print(json.dumps({"ok": False, "error": "Usage: init <project_id> [--path /abs/path] [--skip-gsd]"}))
        sys.exit(1)

    project_id = int(args[0])
    custom_path = None
    skip_gsd = False

    i = 1
    while i < len(args):
        if args[i] == "--path" and i + 1 < len(args):
            custom_path = os.path.expanduser(args[i + 1])
            i += 2
        elif args[i] == "--skip-gsd":
            skip_gsd = True
            i += 1
        else:
            i += 1

    conn = _get_db()
    project = _get_project(conn, project_id)
    if not project:
        print(json.dumps({"ok": False, "error": f"Project {project_id} not found"}))
        sys.exit(1)

    # Determine workspace path
    if custom_path:
        workspace = os.path.abspath(custom_path)
    elif project.get("workspace_path"):
        workspace = project["workspace_path"]
    else:
        slug = _slugify(project["name"])
        workspace = os.path.join(DEFAULT_WORKSPACE_ROOT, slug)

    # Create directory
    os.makedirs(workspace, exist_ok=True)

    # Git init
    git_initialized = False
    if not os.path.isdir(os.path.join(workspace, ".git")):
        subprocess.run(["git", "init"], cwd=workspace, capture_output=True)
        git_initialized = True

    # GSD install
    gsd_installed = False
    if not skip_gsd and not _has_gsd(workspace):
        # Check node/npx availability
        npx = shutil.which("npx")
        if npx:
            result = subprocess.run(
                ["npx", "get-shit-done-cc", "--claude", "--local", "--auto"],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=120,
            )
            gsd_installed = result.returncode == 0
        else:
            gsd_installed = False  # npx not available
    elif _has_gsd(workspace):
        gsd_installed = True  # already present

    # Render and write CLAUDE.md
    profile = _get_user_profile(conn)
    interactions = _get_recent_interactions(conn, project.get("client_id"))
    rendered = _render_template(project, profile, interactions)

    claude_md_path = os.path.join(workspace, "CLAUDE.md")
    claude_md_written = False
    managed = _claude_md_is_soy_managed(workspace)

    if managed is None:
        # File doesn't exist — write it
        with open(claude_md_path, "w") as f:
            f.write(rendered)
        claude_md_written = True
    elif managed:
        # File exists and is SoY-managed — overwrite
        with open(claude_md_path, "w") as f:
            f.write(rendered)
        claude_md_written = True
    # else: file exists but user customized it — leave it alone

    # Seed .planning/PROJECT.md if GSD created .planning/ but no PROJECT.md
    project_md_seeded = False
    planning_dir = os.path.join(workspace, ".planning")
    project_md_path = os.path.join(planning_dir, "PROJECT.md")
    if os.path.isdir(planning_dir) and not os.path.isfile(project_md_path):
        desc = project.get("description") or "No description provided."
        client_line = ""
        if project.get("client_name"):
            client_line = f"\n- **Client:** {project['client_name']}"
            if project.get("client_company"):
                client_line += f" ({project['client_company']})"

        project_md_content = f"""# {project['name']}

## Overview
{desc}

## Context
- **Status:** {project.get('status', 'active')}
- **Priority:** {project.get('priority', 'medium')}{client_line}
- **Start date:** {project.get('start_date') or '—'}
- **Target date:** {project.get('target_date') or '—'}

## Goals
_Define project goals here._

## Constraints
_Define constraints, budget, timeline, or technical requirements here._
"""
        with open(project_md_path, "w") as f:
            f.write(project_md_content)
        project_md_seeded = True

    # Update workspace_path in DB
    conn.execute(
        "UPDATE projects SET workspace_path = ?, updated_at = datetime('now') WHERE id = ?",
        (workspace, project_id),
    )

    # Log activity
    conn.execute(
        """
        INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
        VALUES ('project', ?, 'workspace_initialized', ?, datetime('now'))
        """,
        (
            project_id,
            json.dumps({
                "workspace": workspace,
                "git_initialized": git_initialized,
                "gsd_installed": gsd_installed,
                "claude_md_written": claude_md_written,
            }),
        ),
    )

    conn.commit()
    conn.close()

    print(json.dumps({
        "ok": True,
        "workspace": workspace,
        "git_initialized": git_initialized,
        "gsd_installed": gsd_installed,
        "claude_md_written": claude_md_written,
        "project_md_seeded": project_md_seeded,
    }))


def cmd_status(args):
    """Check workspace status for a project."""
    if not args:
        print(json.dumps({"ok": False, "error": "Usage: status <project_id>"}))
        sys.exit(1)

    project_id = int(args[0])
    conn = _get_db()
    project = _get_project(conn, project_id)
    conn.close()

    if not project:
        print(json.dumps({"ok": False, "error": f"Project {project_id} not found"}))
        sys.exit(1)

    workspace = project.get("workspace_path")
    if not workspace:
        print(json.dumps({
            "ok": True,
            "project_id": project_id,
            "project_name": project["name"],
            "workspace": None,
            "message": "No workspace configured",
        }))
        return

    status = {
        "ok": True,
        "project_id": project_id,
        "project_name": project["name"],
        "workspace": workspace,
        "dir_exists": os.path.isdir(workspace),
        "git_initialized": os.path.isdir(os.path.join(workspace, ".git")),
        "gsd_installed": _has_gsd(workspace),
        "has_planning": _has_planning(workspace),
        "claude_md_exists": os.path.isfile(os.path.join(workspace, "CLAUDE.md")),
        "claude_md_soy_managed": _claude_md_is_soy_managed(workspace) or False,
    }

    # Check for STATE.md if .planning/ exists
    if status["has_planning"]:
        status["has_state_md"] = os.path.isfile(
            os.path.join(workspace, ".planning", "STATE.md")
        )
        status["has_roadmap_md"] = os.path.isfile(
            os.path.join(workspace, ".planning", "ROADMAP.md")
        )

    print(json.dumps(status))


def cmd_refresh(args):
    """Re-render CLAUDE.md with fresh data (only if SOY-MANAGED)."""
    if not args:
        print(json.dumps({"ok": False, "error": "Usage: refresh <project_id>"}))
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

    managed = _claude_md_is_soy_managed(workspace)
    if managed is None:
        # No CLAUDE.md — write one
        pass
    elif not managed:
        print(json.dumps({
            "ok": False,
            "error": "CLAUDE.md exists but SOY-MANAGED sentinel was removed. Not overwriting.",
            "hint": "Add '<!-- SOY-MANAGED -->' to the first line to re-enable managed updates.",
        }))
        conn.close()
        return

    profile = _get_user_profile(conn)
    interactions = _get_recent_interactions(conn, project.get("client_id"))
    rendered = _render_template(project, profile, interactions)

    claude_md_path = os.path.join(workspace, "CLAUDE.md")
    with open(claude_md_path, "w") as f:
        f.write(rendered)

    conn.execute(
        """
        INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
        VALUES ('project', ?, 'claude_md_refreshed', ?, datetime('now'))
        """,
        (project_id, json.dumps({"workspace": workspace})),
    )
    conn.commit()
    conn.close()

    print(json.dumps({"ok": True, "claude_md_refreshed": True, "workspace": workspace}))


def main():
    if len(sys.argv) < 2:
        print("Usage: init_project.py <init|status|refresh> <project_id> [options]")
        sys.exit(1)

    command = sys.argv[1]
    rest = sys.argv[2:]

    commands = {
        "init": cmd_init,
        "status": cmd_status,
        "refresh": cmd_refresh,
    }

    if command not in commands:
        print(json.dumps({"ok": False, "error": f"Unknown command: {command}"}))
        sys.exit(1)

    commands[command](rest)


if __name__ == "__main__":
    main()
