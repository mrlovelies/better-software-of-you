#!/usr/bin/env python3
"""Sync auditions from casting platform emails → local DB.

Finds emails from Casting Workbook, Actors Access, and WeAudition,
fetches full body via Gmail API, parses audition details, creates records.

Usage:
    python3 sync_auditions.py scan      # Find new casting emails → parse → store
    python3 sync_auditions.py pending   # List auditions with status 'new'
    python3 sync_auditions.py list      # List all active auditions
"""

import base64
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from html.parser import HTMLParser


PLUGIN_ROOT = os.environ.get(
    "CLAUDE_PLUGIN_ROOT",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)
DB_PATH = os.path.join(PLUGIN_ROOT, "data", "soy.db")

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"

# Jackie Warden's contact ID — agent for all casting platform auditions
AGENT_CONTACT_ID = 2

# Email detection patterns
# Note: Actors Access CMail notifications come as bare "CMail message!" emails
# from various AA addresses. The snippet contains "From: <agent> Subject: <project>".
# We detect these by subject pattern since the from_address varies.
CASTING_SOURCES = {
    "castingworkbook": {
        "from_match": "no-reply@castingworkbook.com",
        "subject_match": r"Recording request for",
    },
    "actorsaccess": {
        "from_match": "actorsaccess",
        "subject_match": r"CMail message",
    },
    "weaudition": {
        "from_match": "weaudition",
        "subject_match": None,  # match by from_address only
    },
}

# CMail notifications may come from addresses that don't contain "actorsaccess"
# (e.g., generic notification addresses). Detect by subject pattern alone.
CMAIL_SUBJECT_RE = re.compile(r"CMail message", re.IGNORECASE)


# ── HTML Text Extractor ──────────────────────────────────────────────────


class HTMLTextExtractor(HTMLParser):
    """Simple HTML-to-text converter using stdlib html.parser."""

    def __init__(self):
        super().__init__()
        self._text = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True
        elif tag == "br":
            self._text.append("\n")
        elif tag in ("p", "div", "tr", "li"):
            self._text.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            self._text.append(data)

    def get_text(self):
        return "".join(self._text).strip()


def html_to_text(html):
    """Convert HTML to plain text."""
    extractor = HTMLTextExtractor()
    extractor.feed(html)
    return extractor.get_text()


# ── Helpers ──────────────────────────────────────────────────────────────


def _get_token(email=None):
    """Get a valid OAuth token via google_auth.py."""
    auth_script = os.path.join(PLUGIN_ROOT, "shared", "google_auth.py")
    import subprocess

    cmd = [sys.executable, auth_script, "token"]
    if email:
        cmd.append(email)

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    token = result.stdout.strip()
    if token.startswith("{"):
        return None
    return token


def _get_active_accounts():
    """Get list of active Google accounts from the database."""
    try:
        conn = _get_db()
        rows = conn.execute(
            "SELECT email FROM google_accounts WHERE status = 'active'"
        ).fetchall()
        conn.close()
        return [r["email"] for r in rows]
    except Exception:
        return []


def _api_get(url, token):
    """Authenticated GET request to a Google API."""
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def _get_db():
    """Get a SQLite connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _decode_base64url(data):
    """Decode base64url-encoded data (Gmail body encoding)."""
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


def _extract_body_parts(payload, mime_type="text/html"):
    """Recursively walk Gmail payload parts to find the body of a given MIME type."""
    if payload.get("mimeType") == mime_type:
        body_data = payload.get("body", {}).get("data", "")
        if body_data:
            return _decode_base64url(body_data)

    for part in payload.get("parts", []):
        result = _extract_body_parts(part, mime_type)
        if result:
            return result

    return None


def _detect_source(from_address, subject):
    """Detect which casting platform an email is from. Returns source key or None."""
    from_lower = (from_address or "").lower()
    subject_lower = (subject or "").lower()

    for source_key, patterns in CASTING_SOURCES.items():
        from_match = patterns["from_match"].lower()
        subject_pattern = patterns.get("subject_match")

        if from_match in from_lower:
            if subject_pattern is None:
                return source_key
            if re.search(subject_pattern, subject_lower, re.IGNORECASE):
                return source_key

    # CMail notifications can come from any address — detect by subject alone
    if CMAIL_SUBJECT_RE.search(subject or ""):
        return "actorsaccess"

    return None


# ── Parsers ──────────────────────────────────────────────────────────────


def _parse_casting_workbook(subject, body_html, snippet):
    """Parse a Casting Workbook 'Recording request for ...' email.

    Subject format: "Recording request for PROJECT_NAME"
    Body contains: role name, casting director, deadline, self-tape specs.
    """
    result = {
        "project_name": None,
        "role_name": None,
        "casting_director": None,
        "casting_company": None,
        "deadline": None,
        "self_tape_specs": None,
        "production_type": None,
        "role_type": None,
        "source_url": None,
    }

    # Extract project name from subject
    subject_match = re.search(r"Recording request for (.+)", subject, re.IGNORECASE)
    if subject_match:
        result["project_name"] = subject_match.group(1).strip().rstrip("-").strip()

    # Parse body text
    text = html_to_text(body_html) if body_html else (snippet or "")

    # Role name — look for "Role:" or "Character:" patterns
    role_match = re.search(r"(?:Role|Character)\s*[:–-]\s*(.+?)(?:\n|$)", text, re.IGNORECASE)
    if role_match:
        result["role_name"] = role_match.group(1).strip()

    # Casting director
    cd_match = re.search(r"(?:Casting Director|CD|Casting By)\s*[:–-]\s*(.+?)(?:\n|$)", text, re.IGNORECASE)
    if cd_match:
        result["casting_director"] = cd_match.group(1).strip()

    # Casting company
    cc_match = re.search(r"(?:Casting Company|Company)\s*[:–-]\s*(.+?)(?:\n|$)", text, re.IGNORECASE)
    if cc_match:
        result["casting_company"] = cc_match.group(1).strip()

    # Deadline — look for date patterns near "deadline", "due", "by"
    deadline_match = re.search(
        r"(?:deadline|due|by|submit by|submission deadline)\s*[:–-]?\s*"
        r"(\w+ \d{1,2},?\s*\d{4}|\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})",
        text, re.IGNORECASE
    )
    if deadline_match:
        date_str = deadline_match.group(1)
        for fmt in ("%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y", "%m/%d/%Y", "%Y-%m-%d"):
            try:
                result["deadline"] = datetime.strptime(date_str, fmt).strftime("%Y-%m-%d %H:%M:%S")
                break
            except ValueError:
                continue

    # Self-tape specs — look for recording instructions
    specs_match = re.search(
        r"(?:self.?tape|recording|instructions|specs|format)\s*[:–-]?\s*(.+?)(?=\n\n|\Z)",
        text, re.IGNORECASE | re.DOTALL
    )
    if specs_match:
        result["self_tape_specs"] = specs_match.group(1).strip()[:500]

    # Production type from snippet/body context
    text_lower = text.lower()
    if "audible" in text_lower or "audiobook" in text_lower:
        result["production_type"] = "audiobook"
    elif "commercial" in text_lower:
        result["production_type"] = "commercial"
    elif "series" in text_lower or " s " in text_lower or re.search(r"s\d+", text_lower):
        result["production_type"] = "tv"
    elif "film" in text_lower or "feature" in text_lower:
        result["production_type"] = "film"

    # Source URL — look for links to castingworkbook.com
    url_match = re.search(r"(https?://[^\s\"<>]*castingworkbook\.com[^\s\"<>]*)", body_html or "")
    if url_match:
        result["source_url"] = url_match.group(1)

    # Fallback: if no project name from subject, try snippet
    if not result["project_name"] and snippet:
        result["project_name"] = snippet[:80].strip()

    return result


def _parse_actors_access(subject, body_html, snippet):
    """Parse an Actors Access CMail message.

    CMail notifications are bare emails — the actual audition details live on
    the AA website. The snippet typically contains:
        "You have a CMail message From: Jackie Warden Subject: Re: ECO CAST: COSMIC"

    We extract the project name from the snippet's "Subject:" line and flag
    the audition as needing manual detail entry from the AA website.
    """
    result = {
        "project_name": None,
        "role_name": None,
        "casting_director": None,
        "casting_company": None,
        "deadline": None,
        "self_tape_specs": None,
        "production_type": None,
        "role_type": None,
        "source_url": None,
        "needs_details": True,  # flag for CMail — details on AA website
    }

    text = html_to_text(body_html) if body_html else (snippet or "")

    # ── Try to extract from CMail notification snippet ──
    # Pattern: "Subject: Re: ECO CAST: COSMIC Log in to Actors Access..."
    # We need to stop before "Log in" or end-of-string
    snippet_subj = re.search(
        r"Subject:\s*(?:Re:\s*)?(.+?)(?:\s+Log in to|\s*$|\s*\n)",
        snippet or "", re.IGNORECASE
    )
    if snippet_subj:
        result["project_name"] = snippet_subj.group(1).strip()

    # ── Try structured field extraction from full body (if fetched) ──
    if body_html:
        # Project name from body — look for "Project:" or bold title
        proj_match = re.search(r"(?:Project|Production|Show)\s*[:–-]\s*(.+?)(?:\n|$)", text, re.IGNORECASE)
        if proj_match:
            result["project_name"] = proj_match.group(1).strip()
            result["needs_details"] = False  # full details available

        # Role
        role_match = re.search(r"(?:Role|Character)\s*[:–-]\s*(.+?)(?:\n|$)", text, re.IGNORECASE)
        if role_match:
            result["role_name"] = role_match.group(1).strip()

        # Role type
        rt_match = re.search(r"(?:Role Type|Type)\s*[:–-]\s*(.+?)(?:\n|$)", text, re.IGNORECASE)
        if rt_match:
            role_type_raw = rt_match.group(1).strip().lower()
            for rt in ("lead", "supporting", "guest", "background", "voiceover"):
                if rt in role_type_raw:
                    result["role_type"] = rt
                    break

        # Casting director
        cd_match = re.search(r"(?:Casting Director|CD|Casting)\s*[:–-]\s*(.+?)(?:\n|$)", text, re.IGNORECASE)
        if cd_match:
            result["casting_director"] = cd_match.group(1).strip()

        # Deadline
        deadline_match = re.search(
            r"(?:deadline|due|submit by)\s*[:–-]?\s*"
            r"(\w+ \d{1,2},?\s*\d{4}|\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})",
            text, re.IGNORECASE
        )
        if deadline_match:
            date_str = deadline_match.group(1)
            for fmt in ("%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y", "%m/%d/%Y", "%Y-%m-%d"):
                try:
                    result["deadline"] = datetime.strptime(date_str, fmt).strftime("%Y-%m-%d %H:%M:%S")
                    break
                except ValueError:
                    continue

        # Source URL
        url_match = re.search(r"(https?://[^\s\"<>]*actorsaccess\.com[^\s\"<>]*)", body_html)
        if url_match:
            result["source_url"] = url_match.group(1)

    # Fallback project name
    if not result["project_name"]:
        # Try extracting from the email subject itself
        subj_match = re.search(r"CMail message!?\s*[-–:]?\s*(.*)", subject, re.IGNORECASE)
        if subj_match and subj_match.group(1).strip():
            result["project_name"] = subj_match.group(1).strip()
        elif snippet:
            result["project_name"] = snippet[:80].strip()

    return result


def _parse_weaudition(subject, body_html, snippet):
    """Parse a WeAudition email."""
    result = {
        "project_name": None,
        "role_name": None,
        "casting_director": None,
        "casting_company": None,
        "deadline": None,
        "self_tape_specs": None,
        "production_type": None,
        "role_type": None,
        "source_url": None,
    }

    text = html_to_text(body_html) if body_html else (snippet or "")

    # Try standard field patterns
    for field, patterns in [
        ("project_name", [r"(?:Project|Production|Title)\s*[:–-]\s*(.+?)(?:\n|$)"]),
        ("role_name", [r"(?:Role|Character)\s*[:–-]\s*(.+?)(?:\n|$)"]),
        ("casting_director", [r"(?:Casting Director|CD)\s*[:–-]\s*(.+?)(?:\n|$)"]),
    ]:
        for pattern in patterns:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                result[field] = m.group(1).strip()
                break

    # Fallback project name from subject
    if not result["project_name"]:
        result["project_name"] = subject.strip() if subject else (snippet[:80] if snippet else "Unknown")

    # Source URL
    url_match = re.search(r"(https?://[^\s\"<>]*weaudition\.com[^\s\"<>]*)", body_html or "")
    if url_match:
        result["source_url"] = url_match.group(1)

    return result


def _parse_generic(subject, body_html, snippet):
    """Fallback parser — extract what we can from any casting email."""
    result = {
        "project_name": subject.strip() if subject else (snippet[:80] if snippet else "Unknown"),
        "role_name": None,
        "casting_director": None,
        "casting_company": None,
        "deadline": None,
        "self_tape_specs": None,
        "production_type": None,
        "role_type": None,
        "source_url": None,
    }

    text = html_to_text(body_html) if body_html else (snippet or "")

    role_match = re.search(r"(?:Role|Character)\s*[:–-]\s*(.+?)(?:\n|$)", text, re.IGNORECASE)
    if role_match:
        result["role_name"] = role_match.group(1).strip()

    cd_match = re.search(r"(?:Casting Director|CD)\s*[:–-]\s*(.+?)(?:\n|$)", text, re.IGNORECASE)
    if cd_match:
        result["casting_director"] = cd_match.group(1).strip()

    return result


PARSERS = {
    "castingworkbook": _parse_casting_workbook,
    "actorsaccess": _parse_actors_access,
    "weaudition": _parse_weaudition,
}


# ── Commands ─────────────────────────────────────────────────────────────


def _scan_with_token(token, conn):
    """Scan for casting emails using a specific token. Returns (imported, errors)."""
    imported = []
    errors = []

    # Find unprocessed casting emails (not already in audition_sources)
    # Includes CMail notifications detected by subject pattern (any from_address)
    casting_emails = conn.execute(
        """SELECT e.id, e.gmail_id, e.subject, e.snippet, e.from_address, e.from_name, e.received_at
           FROM emails e
           WHERE (
               e.from_address = 'no-reply@castingworkbook.com'
               OR e.from_address LIKE '%actorsaccess%'
               OR e.from_address LIKE '%weaudition%'
               OR e.subject LIKE '%CMail message%'
           )
           AND e.id NOT IN (SELECT email_id FROM audition_sources WHERE email_id IS NOT NULL)
           ORDER BY e.received_at DESC"""
    ).fetchall()

    if not casting_emails:
        return imported, errors

    for email in casting_emails:
        email_id = email["id"]
        gmail_id = email["gmail_id"]
        subject = email["subject"] or ""
        snippet = email["snippet"] or ""
        from_address = email["from_address"] or ""
        received_at = email["received_at"]

        # Detect source platform
        source = _detect_source(from_address, subject)
        if not source:
            continue

        # CMail filter: skip notifications not addressed to the user
        # (Jackie's roster includes other actors — e.g., "STELLA, You have a CMail...")
        if source == "actorsaccess" and "CMail message" in subject:
            if snippet and not snippet.upper().startswith("ALEX"):
                continue

        try:
            # Fetch full email body via Gmail API
            body_html = None
            try:
                msg = _api_get(
                    f"{GMAIL_API}/messages/{gmail_id}?format=full",
                    token,
                )
                body_html = _extract_body_parts(msg.get("payload", {}), "text/html")
                if not body_html:
                    body_html = _extract_body_parts(msg.get("payload", {}), "text/plain")
            except Exception as e:
                # Fall back to snippet-only parsing
                errors.append({"email_id": email_id, "warning": f"Couldn't fetch body: {e}"})

            # Parse with source-specific parser
            parser = PARSERS.get(source, _parse_generic)
            parsed = parser(subject, body_html, snippet)

            if not parsed.get("project_name"):
                parsed["project_name"] = subject or "Unknown Project"

            # Set notes for CMail notifications that need manual detail entry
            notes = parsed.get("self_tape_specs")  # preserve if set
            needs_details = parsed.get("needs_details", False)
            if needs_details:
                notes = "⚠ Details needed — check Actors Access website for full breakdown"

            # Create audition record
            cursor = conn.execute(
                """INSERT INTO auditions
                   (project_name, role_name, role_type, production_type,
                    casting_director, casting_company, agent_contact_id,
                    source, source_email_id, source_url, status,
                    received_at, deadline, notes, self_tape_specs)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?, ?, ?)""",
                (
                    parsed["project_name"],
                    parsed["role_name"],
                    parsed["role_type"],
                    parsed["production_type"],
                    parsed["casting_director"],
                    parsed["casting_company"],
                    AGENT_CONTACT_ID,
                    source,
                    email_id,
                    parsed["source_url"],
                    received_at,
                    parsed["deadline"],
                    notes,
                    parsed["self_tape_specs"] if not needs_details else None,
                ),
            )
            audition_id = cursor.lastrowid

            # Record in audition_sources for dedup
            conn.execute(
                """INSERT INTO audition_sources (audition_id, email_id, source_type)
                   VALUES (?, ?, ?)""",
                (audition_id, email_id, "casting_email"),
            )

            # Log activity
            conn.execute(
                """INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
                   VALUES ('audition', ?, 'auto_imported',
                           json_object('project', ?, 'source', ?, 'role', ?),
                           datetime('now'))""",
                (audition_id, parsed["project_name"], source, parsed["role_name"]),
            )

            conn.commit()

            imported.append({
                "audition_id": audition_id,
                "project_name": parsed["project_name"],
                "role_name": parsed["role_name"],
                "source": source,
                "casting_director": parsed["casting_director"],
                "received_at": received_at,
                "needs_details": needs_details,
            })

        except Exception as e:
            errors.append({"email_id": email_id, "error": str(e)})
            conn.rollback()

    return imported, errors


def cmd_scan():
    """Find new casting emails, fetch full body, parse, create audition records."""
    conn = _get_db()
    all_imported = []
    all_errors = []

    accounts = _get_active_accounts()

    if accounts:
        for account_email in accounts:
            token = _get_token(email=account_email)
            if not token:
                all_errors.append({"account": account_email, "error": "Token invalid"})
                continue
            imported, errors = _scan_with_token(token, conn)
            all_imported.extend(imported)
            all_errors.extend(errors)
    else:
        token = _get_token()
        if not token:
            print(json.dumps({"error": "Not authenticated. Run /google-setup first."}))
            conn.close()
            sys.exit(1)
        imported, errors = _scan_with_token(token, conn)
        all_imported.extend(imported)
        all_errors.extend(errors)

    # Update last scanned timestamp
    conn.execute(
        """INSERT OR REPLACE INTO soy_meta (key, value, updated_at)
           VALUES ('auditions_last_scanned', datetime('now'), datetime('now'))"""
    )
    conn.commit()
    conn.close()

    print(json.dumps({
        "imported": len(all_imported),
        "auditions": all_imported,
        "errors": all_errors,
    }))


def cmd_pending():
    """List auditions with status 'new'."""
    conn = _get_db()
    rows = conn.execute(
        """SELECT id, project_name, role_name, source, casting_director,
                  received_at, deadline, urgency, days_until_deadline
           FROM v_audition_pipeline
           WHERE status = 'new'
           ORDER BY COALESCE(deadline, '9999-12-31') ASC"""
    ).fetchall()
    conn.close()

    auditions = [
        {
            "id": r["id"],
            "project_name": r["project_name"],
            "role_name": r["role_name"],
            "source": r["source"],
            "casting_director": r["casting_director"],
            "received_at": r["received_at"],
            "deadline": r["deadline"],
            "urgency": r["urgency"],
            "days_until_deadline": r["days_until_deadline"],
        }
        for r in rows
    ]
    print(json.dumps({"pending": len(auditions), "auditions": auditions}))


def cmd_list():
    """List all active (non-passed, non-expired) auditions."""
    conn = _get_db()
    rows = conn.execute(
        """SELECT id, project_name, role_name, role_type, production_type,
                  source, status, casting_director, received_at, deadline,
                  urgency, days_until_deadline, days_since_received, agent_name
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
               COALESCE(deadline, '9999-12-31') ASC"""
    ).fetchall()
    conn.close()

    auditions = [dict(r) for r in rows]
    print(json.dumps({"active": len(auditions), "auditions": auditions}))


# ── Main ─────────────────────────────────────────────────────────────────


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: sync_auditions.py [scan|pending|list]"}))
        sys.exit(1)

    command = sys.argv[1]

    if command == "scan":
        cmd_scan()
    elif command == "pending":
        cmd_pending()
    elif command == "list":
        cmd_list()
    else:
        print(json.dumps({"error": f"Unknown command: {command}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
