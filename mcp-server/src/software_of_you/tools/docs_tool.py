"""Google Docs tool — create, read, edit, list, and export Google Docs."""

import json
import urllib.request
from mcp.server.fastmcp import FastMCP

from software_of_you.db import execute, execute_write, execute_many, rows_to_dicts
from software_of_you.google_auth import get_valid_token

DOCS_API = "https://docs.googleapis.com/v1/documents"
DRIVE_API = "https://www.googleapis.com/drive/v3/files"


def register(server: FastMCP) -> None:
    @server.tool()
    def docs(
        action: str,
        title: str = "",
        content: str = "",
        doc_id: str = "",
        query: str = "",
        contact_id: int = 0,
        project_id: int = 0,
        doc_type: str = "general",
        limit: int = 20,
    ) -> dict:
        """Create, read, edit, list, and export Google Docs.

        Actions:
          create   — Create a new Google Doc (title required, content optional)
          read     — Read a document's content (doc_id required)
          edit     — Append or replace content in a doc (doc_id and content required)
          list     — List tracked documents (optional: contact_id, project_id, doc_type)
          search   — Search documents by title (query required)
          link     — Link an existing doc to a contact/project (doc_id required, contact_id/project_id)
          export   — Create a new doc from HTML/text content (title and content required)
          sync     — Fetch latest doc metadata from Google and update local DB

        Returns document data, content, or operation results.
        """
        try:
            if action == "create":
                return _create(title, content, contact_id, project_id, doc_type)
            elif action == "read":
                return _read(doc_id)
            elif action == "edit":
                return _edit(doc_id, content)
            elif action == "list":
                return _list(contact_id, project_id, doc_type, limit)
            elif action == "search":
                return _search(query, limit)
            elif action == "link":
                return _link(doc_id, contact_id, project_id, doc_type)
            elif action == "export":
                return _export(title, content, contact_id, project_id)
            elif action == "sync":
                return _sync_doc(doc_id)
            else:
                return {"error": f"Unknown action: {action}. Use: create, read, edit, list, search, link, export, sync"}
        except Exception as e:
            return {"error": str(e)}


def _api_request(url: str, token: str, method: str = "GET", data: dict = None) -> dict:
    """Make an authenticated request to a Google API."""
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _extract_text(doc: dict) -> str:
    """Extract plain text from a Google Docs API document response."""
    text_parts = []
    for element in doc.get("body", {}).get("content", []):
        paragraph = element.get("paragraph")
        if not paragraph:
            continue
        line_parts = []
        for pe in paragraph.get("elements", []):
            text_run = pe.get("textRun")
            if text_run:
                line_parts.append(text_run.get("content", ""))
        text_parts.append("".join(line_parts))
    return "".join(text_parts).strip()


def _track_doc(google_doc_id: str, title: str, url: str, contact_id: int = 0, project_id: int = 0, doc_type: str = "general", content_preview: str = "") -> int:
    """Insert or update a doc in the local tracking table."""
    existing = execute("SELECT id FROM google_docs WHERE google_doc_id = ?", (google_doc_id,))
    if existing:
        execute_write(
            """UPDATE google_docs SET title = ?, url = ?, contact_id = CASE WHEN ? > 0 THEN ? ELSE contact_id END,
               project_id = CASE WHEN ? > 0 THEN ? ELSE project_id END,
               doc_type = ?, content_preview = ?, last_synced_at = datetime('now'), updated_at = datetime('now')
               WHERE google_doc_id = ?""",
            (title, url, contact_id, contact_id, project_id, project_id, doc_type, content_preview[:500] if content_preview else None, google_doc_id),
        )
        return existing[0]["id"]
    else:
        return execute_write(
            """INSERT INTO google_docs (google_doc_id, title, url, contact_id, project_id, doc_type, content_preview, last_synced_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (google_doc_id, title, url, contact_id if contact_id > 0 else None, project_id if project_id > 0 else None, doc_type, content_preview[:500] if content_preview else None),
        )


def _create(title: str, content: str, contact_id: int, project_id: int, doc_type: str) -> dict:
    if not title:
        return {"error": "title is required to create a document"}

    token = get_valid_token()
    if not token:
        return {"error": "Google not connected. Use /google-setup first."}

    doc = _api_request(DOCS_API, token, method="POST", data={"title": title})
    google_doc_id = doc["documentId"]
    url = f"https://docs.google.com/document/d/{google_doc_id}/edit"

    if content:
        _insert_text(google_doc_id, content, token)

    local_id = _track_doc(google_doc_id, title, url, contact_id, project_id, doc_type, content[:500] if content else "")

    execute_write(
        "INSERT INTO activity_log (entity_type, entity_id, action, details, created_at) VALUES ('google_doc', ?, 'created', ?, datetime('now'))",
        (local_id, f"Created Google Doc: {title}"),
    )

    return {
        "result": {"id": local_id, "google_doc_id": google_doc_id, "title": title, "url": url},
        "count": 1,
        "_context": {
            "presentation": f"Created **{title}**\n{url}",
            "suggestions": ["Open the doc in your browser", "Link it to a contact or project"],
        },
    }


def _insert_text(google_doc_id: str, text: str, token: str) -> None:
    """Insert text at the beginning of a document (after the initial newline)."""
    _api_request(
        f"{DOCS_API}/{google_doc_id}:batchUpdate",
        token,
        method="POST",
        data={
            "requests": [
                {
                    "insertText": {
                        "location": {"index": 1},
                        "text": text,
                    }
                }
            ]
        },
    )


def _read(doc_id: str) -> dict:
    if not doc_id:
        return {"error": "doc_id is required (Google Doc ID or local numeric ID)"}

    google_doc_id = _resolve_doc_id(doc_id)
    if not google_doc_id:
        return {"error": f"Document not found: {doc_id}"}

    token = get_valid_token()
    if not token:
        return {"error": "Google not connected. Use /google-setup first."}

    doc = _api_request(f"{DOCS_API}/{google_doc_id}", token)
    text = _extract_text(doc)
    title = doc.get("title", "Untitled")

    _track_doc(google_doc_id, title, f"https://docs.google.com/document/d/{google_doc_id}/edit", content_preview=text[:500])

    return {
        "result": {"google_doc_id": google_doc_id, "title": title, "content": text},
        "count": 1,
        "_context": {
            "presentation": f"**{title}**\n\n{text[:2000]}{'...' if len(text) > 2000 else ''}",
            "suggestions": ["Edit this document", "Link to a contact or project"],
        },
    }


def _edit(doc_id: str, content: str) -> dict:
    if not doc_id or not content:
        return {"error": "doc_id and content are required"}

    google_doc_id = _resolve_doc_id(doc_id)
    if not google_doc_id:
        return {"error": f"Document not found: {doc_id}"}

    token = get_valid_token()
    if not token:
        return {"error": "Google not connected. Use /google-setup first."}

    # Read current doc to get end index, then clear and rewrite
    doc = _api_request(f"{DOCS_API}/{google_doc_id}", token)
    body_content = doc.get("body", {}).get("content", [])
    if body_content:
        end_index = body_content[-1].get("endIndex", 1)
        requests = []
        if end_index > 2:
            requests.append({
                "deleteContentRange": {
                    "range": {"startIndex": 1, "endIndex": end_index - 1}
                }
            })
        requests.append({
            "insertText": {
                "location": {"index": 1},
                "text": content,
            }
        })
        _api_request(
            f"{DOCS_API}/{google_doc_id}:batchUpdate",
            token,
            method="POST",
            data={"requests": requests},
        )

    title = doc.get("title", "Untitled")
    _track_doc(google_doc_id, title, f"https://docs.google.com/document/d/{google_doc_id}/edit", content_preview=content[:500])

    return {
        "result": {"google_doc_id": google_doc_id, "title": title, "updated": True},
        "count": 1,
        "_context": {
            "presentation": f"Updated **{title}**",
            "suggestions": ["Read the document to verify", "Share the link"],
        },
    }


def _list(contact_id: int, project_id: int, doc_type: str, limit: int) -> dict:
    conditions = []
    params = []
    if contact_id > 0:
        conditions.append("gd.contact_id = ?")
        params.append(contact_id)
    if project_id > 0:
        conditions.append("gd.project_id = ?")
        params.append(project_id)
    if doc_type and doc_type != "general":
        conditions.append("gd.doc_type = ?")
        params.append(doc_type)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)

    rows = execute(
        f"""SELECT gd.*, c.name as contact_name, p.name as project_name
            FROM google_docs gd
            LEFT JOIN contacts c ON gd.contact_id = c.id
            LEFT JOIN projects p ON gd.project_id = p.id
            {where}
            ORDER BY gd.updated_at DESC LIMIT ?""",
        tuple(params),
    )
    results = rows_to_dicts(rows)
    return {
        "result": results,
        "count": len(results),
        "_context": {
            "presentation": "table",
            "suggestions": ["Read a specific document", "Create a new document"],
        },
    }


def _search(query: str, limit: int) -> dict:
    if not query:
        return {"error": "query is required for search"}

    rows = execute(
        """SELECT gd.*, c.name as contact_name, p.name as project_name
           FROM google_docs gd
           LEFT JOIN contacts c ON gd.contact_id = c.id
           LEFT JOIN projects p ON gd.project_id = p.id
           WHERE gd.title LIKE ? OR gd.content_preview LIKE ?
           ORDER BY gd.updated_at DESC LIMIT ?""",
        (f"%{query}%", f"%{query}%", limit),
    )
    results = rows_to_dicts(rows)
    return {
        "result": results,
        "count": len(results),
        "_context": {
            "presentation": "table",
            "suggestions": ["Read a specific document"] if results else ["Create a new document"],
        },
    }


def _link(doc_id: str, contact_id: int, project_id: int, doc_type: str) -> dict:
    if not doc_id:
        return {"error": "doc_id is required"}
    if contact_id == 0 and project_id == 0:
        return {"error": "Provide contact_id or project_id to link"}

    google_doc_id = _resolve_doc_id(doc_id)
    if not google_doc_id:
        # Not tracked yet — add it
        token = get_valid_token()
        if not token:
            return {"error": "Google not connected. Use /google-setup first."}
        doc = _api_request(f"{DOCS_API}/{doc_id}", token)
        google_doc_id = doc["documentId"]
        title = doc.get("title", "Untitled")
        url = f"https://docs.google.com/document/d/{google_doc_id}/edit"
        _track_doc(google_doc_id, title, url, contact_id, project_id, doc_type)
    else:
        updates = []
        params = []
        if contact_id > 0:
            updates.append("contact_id = ?")
            params.append(contact_id)
        if project_id > 0:
            updates.append("project_id = ?")
            params.append(project_id)
        if doc_type:
            updates.append("doc_type = ?")
            params.append(doc_type)
        updates.append("updated_at = datetime('now')")
        params.append(google_doc_id)
        execute_write(f"UPDATE google_docs SET {', '.join(updates)} WHERE google_doc_id = ?", tuple(params))

    return {
        "result": {"google_doc_id": google_doc_id, "linked": True, "contact_id": contact_id, "project_id": project_id},
        "count": 1,
        "_context": {
            "presentation": "Linked document to contact/project.",
            "suggestions": ["View the document", "List all linked docs"],
        },
    }


def _export(title: str, content: str, contact_id: int, project_id: int) -> dict:
    """Create a new Google Doc from provided content (HTML or plain text)."""
    if not title or not content:
        return {"error": "title and content are required for export"}

    return _create(title, content, contact_id, project_id, "export")


def _sync_doc(doc_id: str) -> dict:
    """Fetch latest metadata for a tracked doc from Google."""
    if not doc_id:
        return {"error": "doc_id is required"}

    google_doc_id = _resolve_doc_id(doc_id)
    if not google_doc_id:
        return {"error": f"Document not found locally: {doc_id}"}

    token = get_valid_token()
    if not token:
        return {"error": "Google not connected. Use /google-setup first."}

    doc = _api_request(f"{DOCS_API}/{google_doc_id}", token)
    text = _extract_text(doc)
    title = doc.get("title", "Untitled")
    _track_doc(google_doc_id, title, f"https://docs.google.com/document/d/{google_doc_id}/edit", content_preview=text[:500])

    return {
        "result": {"google_doc_id": google_doc_id, "title": title, "synced": True},
        "count": 1,
        "_context": {
            "presentation": f"Synced **{title}**",
            "suggestions": ["Read the full content"],
        },
    }


def _resolve_doc_id(doc_id: str) -> str | None:
    """Resolve a local ID or Google Doc ID to a Google Doc ID."""
    if doc_id.isdigit():
        rows = execute("SELECT google_doc_id FROM google_docs WHERE id = ?", (int(doc_id),))
        return rows[0]["google_doc_id"] if rows else None
    else:
        rows = execute("SELECT google_doc_id FROM google_docs WHERE google_doc_id = ?", (doc_id,))
        return rows[0]["google_doc_id"] if rows else doc_id
