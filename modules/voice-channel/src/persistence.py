"""Persistence layer for the voice channel.

Writes call data to SoY's database from Vapi webhook events:
- voice_calls: per-call audit log
- voice_events: per-call event stream (tool invocations, transcripts, errors)
- transcripts: full conversation text (so conversation-intelligence picks it up)
- contacts: auto-create contacts for new caller phone numbers
- contact_interactions: log the call as an interaction
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import phonenumbers  # type: ignore
except ImportError:
    phonenumbers = None  # type: ignore

log = logging.getLogger("voice-channel.persistence")


def get_db(db_path: Path) -> sqlite3.Connection:
    """Open a SoY database connection with WAL mode and busy timeout."""
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")
    return db


# ---------------------------------------------------------------------------
# Phone normalization & owner identity
# ---------------------------------------------------------------------------
#
# Both tools.py and this module need to (a) normalize phone numbers to a
# canonical form for matching, and (b) recognize when the inbound caller is
# actually the SoY owner calling their own line. Both lived in tools.py
# originally, but find_or_create_contact_by_phone needs the same checks at
# the persistence layer to prevent the auto-placeholder bug. Putting them
# here makes persistence the single source of truth — tools.py imports
# from persistence, never the other way around, so there's no cycle.


def normalize_phone(raw: str | None, default_region: str = "CA") -> str | None:
    """Normalize a phone number to E.164 format.

    Uses the phonenumbers library if available (handles all formats reliably:
    "+14169091519", "416.909.1519", "(416) 909-1519", "1-416-909-1519", etc).
    Falls back to basic digit extraction if phonenumbers is not installed.

    Returns None if the input can't be parsed as a valid phone number.
    """
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None

    if phonenumbers is not None:
        try:
            parsed = phonenumbers.parse(raw, default_region)
            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        except phonenumbers.NumberParseException:
            pass
        # Fallthrough to basic normalization if parse fails

    # Basic fallback: strip everything except digits, handle leading "1" for NA
    digits = "".join(c for c in raw if c.isdigit())
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if len(digits) > 0:
        return f"+{digits}"
    return None


def is_owner_phone(db_path: Path, phone: str | None) -> bool:
    """Return True if `phone` matches the SoY owner's own phone number.

    The owner's phone is read from voice_config.owner_transfer_number — the
    field set during install for human-transfer routing. Both sides are
    normalized to E.164 before comparison so storage formats like
    '416.303.0239' or '+1 (416) 303-0239' all match correctly.

    This is the gate that prevents two distinct bugs from happening:
    1. Owner test calls polluting `contacts` with `Caller +1...` placeholders
       (because the owner is not — and should never be — their own contact)
    2. The lookup branch giving a stranger-style greeting when the owner
       calls their own line for testing or admin
    """
    target = normalize_phone(phone)
    if not target:
        return False

    db = get_db(db_path)
    try:
        row = db.execute(
            "SELECT owner_transfer_number FROM voice_config WHERE id = 1"
        ).fetchone()
    finally:
        db.close()

    if not row or not row["owner_transfer_number"]:
        return False
    return normalize_phone(row["owner_transfer_number"]) == target


# ---------------------------------------------------------------------------
# voice_calls — per-call audit log
# ---------------------------------------------------------------------------


def upsert_voice_call(
    db_path: Path,
    *,
    vapi_call_id: str,
    from_number: str | None,
    to_number: str | None,
    started_at: str | None = None,
    assistant_id: str | None = None,
) -> int:
    """Create a voice_calls row or update an existing one. Returns the id."""
    db = get_db(db_path)
    try:
        # Does this call already exist?
        existing = db.execute(
            "SELECT id FROM voice_calls WHERE vapi_call_id = ?",
            (vapi_call_id,),
        ).fetchone()

        if existing:
            return existing["id"]

        cursor = db.execute(
            """
            INSERT INTO voice_calls (
                vapi_call_id, vapi_assistant_id, from_number, to_number,
                started_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            (
                vapi_call_id,
                assistant_id,
                from_number or "",
                to_number or "",
                started_at or datetime.utcnow().isoformat() + "Z",
            ),
        )
        db.commit()
        call_id = cursor.lastrowid
        log.info("Created voice_calls row %d for vapi_call_id=%s", call_id, vapi_call_id)
        return call_id
    finally:
        db.close()


def update_voice_call_outcome(
    db_path: Path,
    *,
    vapi_call_id: str,
    outcome: str,
    outcome_details: str | None = None,
    duration_s: int | None = None,
    ended_at: str | None = None,
    cost_cents: int | None = None,
    cost_breakdown: dict[str, Any] | None = None,
    transcript_id: int | None = None,
    recording_url: str | None = None,
    contact_id: int | None = None,
    booked_event_id: int | None = None,
) -> None:
    """Patch a voice_calls row with outcome data after end-of-call processing."""
    db = get_db(db_path)
    try:
        sets = ["outcome = ?", "updated_at = datetime('now')"]
        params: list[Any] = [outcome]

        if outcome_details is not None:
            sets.append("outcome_details = ?")
            params.append(outcome_details)
        if duration_s is not None:
            sets.append("duration_s = ?")
            params.append(duration_s)
        if ended_at is not None:
            sets.append("ended_at = ?")
            params.append(ended_at)
        if cost_cents is not None:
            sets.append("cost_cents = ?")
            params.append(cost_cents)
        if cost_breakdown is not None:
            sets.append("cost_breakdown_json = ?")
            params.append(json.dumps(cost_breakdown))
        if transcript_id is not None:
            sets.append("transcript_id = ?")
            params.append(transcript_id)
        if recording_url is not None:
            sets.append("recording_url = ?")
            params.append(recording_url)
        if contact_id is not None:
            sets.append("contact_id = ?")
            params.append(contact_id)
        if booked_event_id is not None:
            sets.append("booked_event_id = ?")
            params.append(booked_event_id)

        params.append(vapi_call_id)
        db.execute(
            f"UPDATE voice_calls SET {', '.join(sets)} WHERE vapi_call_id = ?",
            params,
        )
        db.commit()
        log.info("Updated voice_calls outcome for %s: %s", vapi_call_id, outcome)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# voice_events — per-call event stream
# ---------------------------------------------------------------------------


def log_voice_event(
    db_path: Path,
    *,
    call_id: int | None,
    vapi_call_id: str | None,
    event_type: str,
    tool_name: str | None = None,
    data: dict[str, Any] | None = None,
    duration_ms: int | None = None,
) -> None:
    """Append an event to voice_events for the given call."""
    if call_id is None:
        # If we don't have a row yet (event arrived before we tracked the call),
        # try to look up by vapi_call_id
        if vapi_call_id:
            db = get_db(db_path)
            try:
                row = db.execute(
                    "SELECT id FROM voice_calls WHERE vapi_call_id = ?",
                    (vapi_call_id,),
                ).fetchone()
                if row:
                    call_id = row["id"]
            finally:
                db.close()

    if call_id is None:
        log.warning("Cannot log voice_event — no call_id and vapi_call_id %s not found", vapi_call_id)
        return

    db = get_db(db_path)
    try:
        db.execute(
            """
            INSERT INTO voice_events (
                call_id, vapi_call_id, event_type, tool_name, data_json, duration_ms
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                call_id,
                vapi_call_id,
                event_type,
                tool_name,
                json.dumps(data) if data else None,
                duration_ms,
            ),
        )
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Contacts — auto-create from caller phone numbers
# ---------------------------------------------------------------------------


def find_or_create_contact_by_phone(db_path: Path, phone_number: str) -> int | None:
    """Look up a contact by phone number, or create a placeholder.

    Match logic:
    - If the phone is the SoY owner's own number, return None — the owner is
      not their own contact and we must not auto-create a placeholder for
      themselves (see is_owner_phone for context on the bug this prevents)
    - Exact phone match in contacts.phone (E.164 input)
    - Normalized fuzzy match (handles dots, dashes, parens, spaces, missing
      country code) by normalizing both sides to E.164
    - If still no match, create a placeholder contact named "Caller +1..."

    Returns the contact_id or None on failure / owner-self call.
    """
    if not phone_number:
        return None

    # Owner self-call: never auto-create a placeholder. The owner is the
    # operator, not a contact. Caller stays unattributed at the contact
    # layer; the voice_calls row still records the from_number for audit.
    if is_owner_phone(db_path, phone_number):
        log.info(
            "Owner phone %s — refusing to auto-create placeholder (owner is not a contact)",
            phone_number,
        )
        return None

    db = get_db(db_path)
    try:
        # Fast path: exact match (handles already-E.164-stored numbers)
        row = db.execute(
            "SELECT id FROM contacts WHERE phone = ? AND status = 'active' LIMIT 1",
            (phone_number,),
        ).fetchone()
        if row:
            return row["id"]

        # Slow path: normalize the query and every stored phone, then compare.
        # This handles 416.909.1519 vs +14169091519 vs (416) 909-1519 etc.
        normalized_query = normalize_phone(phone_number)
        if normalized_query:
            candidates = db.execute(
                "SELECT id, phone FROM contacts WHERE phone IS NOT NULL AND phone != '' AND status = 'active'"
            ).fetchall()
            for cand in candidates:
                if normalize_phone(cand["phone"]) == normalized_query:
                    return cand["id"]

        # Not found — create a placeholder contact (callers we don't know yet
        # but will recognize on subsequent calls so we can ask their name once)
        store_phone = normalized_query or phone_number
        cursor = db.execute(
            """
            INSERT INTO contacts (name, phone, status, notes, created_at, updated_at)
            VALUES (?, ?, 'active', 'Auto-created from inbound voice call', datetime('now'), datetime('now'))
            """,
            (f"Caller {store_phone}", store_phone),
        )
        db.commit()
        contact_id = cursor.lastrowid
        log.info("Auto-created contact %d for phone %s", contact_id, store_phone)
        return contact_id
    except sqlite3.OperationalError as e:
        log.error("contacts table error during phone lookup: %s", e)
        return None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Transcripts — write the full conversation so conversation-intelligence picks it up
# ---------------------------------------------------------------------------


def _insert_participant(
    db: sqlite3.Connection,
    pcols: list[str],
    *,
    transcript_id: int,
    contact_id: int | None,
    speaker_label: str,
    is_user: int,
) -> None:
    """Insert a transcript_participants row, handling variable schemas."""
    insert_cols = ["transcript_id", "speaker_label"]
    insert_vals: list[Any] = [transcript_id, speaker_label]
    if "contact_id" in pcols:
        insert_cols.append("contact_id")
        insert_vals.append(contact_id)
    if "is_user" in pcols:
        insert_cols.append("is_user")
        insert_vals.append(is_user)
    placeholders = ", ".join(["?"] * len(insert_cols))
    db.execute(
        f"INSERT INTO transcript_participants ({', '.join(insert_cols)}) VALUES ({placeholders})",
        insert_vals,
    )


def write_voice_transcript(
    db_path: Path,
    *,
    vapi_call_id: str,
    contact_id: int | None,
    artifact: dict[str, Any],
    duration_seconds: int | None = None,
) -> int | None:
    """Write the full conversation transcript to SoY's transcripts table.

    The artifact comes from Vapi's end-of-call-report and contains:
    - messages: list of {role, message, time, secondsFromStart, ...}
    - messagesOpenAIFormatted: list of {role, content}
    - recording: optional URL

    We render the conversation as a flat text transcript and store it.
    Conversation-intelligence will pick it up and extract commitments.
    """
    messages = artifact.get("messages", [])
    if not messages:
        return None

    # Render as readable transcript
    lines: list[str] = []
    for m in messages:
        role = m.get("role", "unknown")
        text = (m.get("message") or "").strip()
        if not text:
            continue
        if role == "system":
            continue  # don't include system prompt in the transcript
        speaker = "Caller" if role == "user" else "Assistant" if role == "bot" else role
        lines.append(f"{speaker}: {text}")

    transcript_text = "\n".join(lines)
    if not transcript_text:
        return None

    db = get_db(db_path)
    try:
        # Check if transcripts table exists and what columns it has
        cols = [r["name"] for r in db.execute("PRAGMA table_info(transcripts)").fetchall()]
        if not cols:
            log.warning("transcripts table not found — skipping transcript write")
            return None

        # Build insert dynamically based on available columns
        title = f"Voice call {vapi_call_id[:8]}"
        occurred_at = datetime.utcnow().isoformat() + "Z"

        insert_cols = []
        insert_vals = []

        if "title" in cols:
            insert_cols.append("title")
            insert_vals.append(title)
        if "raw_text" in cols:
            insert_cols.append("raw_text")
            insert_vals.append(transcript_text)
        if "content" in cols:
            insert_cols.append("content")
            insert_vals.append(transcript_text)
        if "occurred_at" in cols:
            insert_cols.append("occurred_at")
            insert_vals.append(occurred_at)
        if "source" in cols:
            insert_cols.append("source")
            insert_vals.append("voice_channel")
        if "duration_seconds" in cols and duration_seconds:
            insert_cols.append("duration_seconds")
            insert_vals.append(duration_seconds)
        if "created_at" in cols:
            insert_cols.append("created_at")
            insert_vals.append(occurred_at)

        if not insert_cols:
            log.warning("transcripts table has no recognized columns — skipping write")
            return None

        placeholders = ", ".join(["?"] * len(insert_cols))
        cursor = db.execute(
            f"INSERT INTO transcripts ({', '.join(insert_cols)}) VALUES ({placeholders})",
            insert_vals,
        )
        db.commit()
        transcript_id = cursor.lastrowid
        log.info("Wrote transcript %d for vapi_call_id=%s (%d chars)", transcript_id, vapi_call_id, len(transcript_text))

        # Link participants via transcript_participants (if the table exists)
        # Caller = the contact (if matched/created), Assistant = the voice bot
        try:
            pcols = [r["name"] for r in db.execute("PRAGMA table_info(transcript_participants)").fetchall()]
            if pcols and "speaker_label" in pcols:
                # Insert caller row
                if contact_id:
                    _insert_participant(
                        db,
                        pcols,
                        transcript_id=transcript_id,
                        contact_id=contact_id,
                        speaker_label="Caller",
                        is_user=0,
                    )
                # Insert assistant row (no contact_id — it's the bot)
                _insert_participant(
                    db,
                    pcols,
                    transcript_id=transcript_id,
                    contact_id=None,
                    speaker_label="Assistant",
                    is_user=1,  # "is_user" = "is the SoY owner side of the conversation"
                )
                db.commit()
        except (sqlite3.OperationalError, sqlite3.IntegrityError) as e:
            log.warning("Could not link transcript_participants: %s", e)
            # Don't fail the whole transcript write over a participants link issue

        return transcript_id
    except (sqlite3.OperationalError, sqlite3.IntegrityError) as e:
        log.error("Failed to write transcript: %s", e)
        return None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Calendar event mirror — write Google events into local calendar_events
# ---------------------------------------------------------------------------


def mirror_calendar_event(
    db_path: Path,
    google_event_data: dict[str, Any],
    account_email: str | None = None,
) -> int | None:
    """Mirror a Google Calendar event into the local calendar_events table.

    Used immediately after a successful book_appointment so the rest of SoY
    sees the new event without waiting for the next pull-sync. The column
    layout matches shared/google_sync.py:sync_calendar so the row looks
    identical to one inserted by the regular sync.

    Args:
        db_path: Path to the SoY database
        google_event_data: The event resource returned by Google's
            calendar.events.insert API (must include 'id' at minimum)
        account_email: Email of the Google account that owns the event,
            used to fill in the account_id FK

    Returns:
        The local calendar_events.id of the inserted/updated row, or None
        on failure.
    """
    event_id = google_event_data.get("id")
    if not event_id:
        log.error("mirror_calendar_event: google_event_data missing 'id'")
        return None

    title = google_event_data.get("summary", "(no title)")
    description = google_event_data.get("description", "") or None
    location = google_event_data.get("location", "") or None

    start = google_event_data.get("start", {})
    end = google_event_data.get("end", {})
    start_time = start.get("dateTime", start.get("date", ""))
    end_time = end.get("dateTime", end.get("date", ""))
    all_day = "date" in start and "dateTime" not in start

    status = google_event_data.get("status", "confirmed")

    attendees_raw = google_event_data.get("attendees", []) or []
    attendees_json = (
        json.dumps(
            [
                {
                    "email": a.get("email", ""),
                    "name": a.get("displayName", ""),
                    "status": a.get("responseStatus", ""),
                }
                for a in attendees_raw
            ]
        )
        if attendees_raw
        else None
    )

    db = get_db(db_path)
    try:
        # Look up account_id from google_accounts (matches sync_calendar pattern).
        # Falls back to NULL if the account isn't registered or the table
        # doesn't exist (e.g., legacy single-account installs).
        account_id: int | None = None
        if account_email:
            try:
                acct_row = db.execute(
                    "SELECT id FROM google_accounts WHERE email = ?",
                    (account_email,),
                ).fetchone()
                if acct_row:
                    account_id = acct_row["id"]
            except sqlite3.OperationalError:
                account_id = None

        # Match attendees to contacts (best-effort)
        contact_ids: list[int] = []
        for a in attendees_raw:
            email = a.get("email", "")
            if not email:
                continue
            try:
                row = db.execute(
                    "SELECT id FROM contacts WHERE email = ? AND status = 'active' LIMIT 1",
                    (email,),
                ).fetchone()
                if row:
                    contact_ids.append(row["id"])
            except sqlite3.OperationalError:
                continue
        contact_ids_json = json.dumps(contact_ids) if contact_ids else None

        db.execute(
            """
            INSERT INTO calendar_events
                (google_event_id, title, description, location, start_time, end_time,
                 all_day, status, attendees, contact_ids, account_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(google_event_id) DO UPDATE SET
                title=excluded.title,
                description=excluded.description,
                location=excluded.location,
                start_time=excluded.start_time,
                end_time=excluded.end_time,
                status=excluded.status,
                attendees=excluded.attendees,
                contact_ids=excluded.contact_ids,
                account_id=COALESCE(excluded.account_id, calendar_events.account_id),
                synced_at=datetime('now')
            """,
            (
                event_id,
                title,
                description,
                location,
                start_time,
                end_time,
                1 if all_day else 0,
                status,
                attendees_json,
                contact_ids_json,
                account_id,
            ),
        )
        db.commit()

        # Look up the local id (whether it was just inserted or already existed)
        row = db.execute(
            "SELECT id FROM calendar_events WHERE google_event_id = ?",
            (event_id,),
        ).fetchone()
        if not row:
            return None
        log.info(
            "Mirrored calendar event %s -> local id %d (%s)",
            event_id,
            row["id"],
            title,
        )
        return row["id"]
    except sqlite3.OperationalError as e:
        log.error("mirror_calendar_event failed: %s", e)
        return None
    finally:
        db.close()


def upgrade_placeholder_contact_name(
    db_path: Path,
    phone: str,
    new_name: str,
) -> bool:
    """Promote a 'Caller +1...' placeholder contact to a real named contact.

    When a stranger calls the voice line and successfully books an appointment,
    we learn their name during the booking flow (passed as book_appointment's
    caller_name argument). Without this helper, the contact stays stuck as
    the auto-created placeholder "Caller +1..." row and every subsequent call
    hits the "good to hear from you again — can I grab your name?" branch
    even though we already have their name from the previous booking.

    Logic:
        1. Find the contact by normalized phone (E.164 match)
        2. Refuse to touch the owner's own contact (defense-in-depth — should
           never be reached because owner bookings won't be strangers, but
           the is_owner_phone check is cheap insurance)
        3. Only upgrade if the contact is a placeholder (name starts with
           "Caller " OR notes contain "Auto-created from inbound voice call")
           — we never overwrite a real named contact with a caller-supplied
           name, because that's a potential spoofing vector
        4. Update name AND append a note so the audit trail is readable

    Returns True if a contact was upgraded, False otherwise (no match,
    already named, owner phone, or SQL error).
    """
    if not phone or not new_name or not new_name.strip():
        return False

    normalized = normalize_phone(phone)
    if not normalized:
        return False

    # Defense in depth: never touch the owner's own contact row
    if is_owner_phone(db_path, normalized):
        return False

    clean_name = new_name.strip()
    if not clean_name:
        return False

    db = get_db(db_path)
    try:
        # Fuzzy-match phone: try exact E.164 first, then normalize every
        # active contact's phone and compare (matches the same pattern as
        # find_or_create_contact_by_phone)
        row = db.execute(
            "SELECT id, name, notes FROM contacts WHERE phone = ? AND status = 'active' LIMIT 1",
            (normalized,),
        ).fetchone()

        if not row:
            candidates = db.execute(
                "SELECT id, name, notes, phone FROM contacts "
                "WHERE phone IS NOT NULL AND phone != '' AND status = 'active'"
            ).fetchall()
            for cand in candidates:
                if normalize_phone(cand["phone"]) == normalized:
                    row = cand
                    break

        if not row:
            log.info("upgrade_placeholder_contact_name: no contact matches %s", phone)
            return False

        current_name = (row["name"] or "").strip()
        current_notes = (row["notes"] or "").lower()
        is_placeholder = (
            current_name.startswith("Caller ")
            or "auto-created from inbound voice call" in current_notes
        )

        if not is_placeholder:
            log.info(
                "upgrade_placeholder_contact_name: contact %d is not a placeholder "
                "(name=%r) — refusing to overwrite",
                row["id"],
                current_name,
            )
            return False

        new_notes = (
            (row["notes"] or "").rstrip()
            + (" " if (row["notes"] or "").strip() else "")
            + f"Name upgraded from placeholder to '{clean_name}' via voice booking on "
            + datetime.utcnow().isoformat()
            + "Z."
        )

        db.execute(
            "UPDATE contacts SET name = ?, notes = ?, updated_at = datetime('now') WHERE id = ?",
            (clean_name, new_notes, row["id"]),
        )
        db.commit()
        log.info(
            "Upgraded placeholder contact %d (was %r) -> %r",
            row["id"],
            current_name,
            clean_name,
        )
        return True
    except sqlite3.OperationalError as e:
        log.error("upgrade_placeholder_contact_name failed: %s", e)
        return False
    finally:
        db.close()


def link_voice_call_to_event(
    db_path: Path,
    vapi_call_id: str,
    calendar_event_id: int,
) -> bool:
    """Set voice_calls.booked_event_id so the audit trail links the call to the booking.

    Returns True if a row was updated, False otherwise.
    """
    if not vapi_call_id or calendar_event_id is None:
        return False

    db = get_db(db_path)
    try:
        cursor = db.execute(
            """
            UPDATE voice_calls
               SET booked_event_id = ?, updated_at = datetime('now')
             WHERE vapi_call_id = ?
            """,
            (calendar_event_id, vapi_call_id),
        )
        db.commit()
        if cursor.rowcount == 0:
            log.warning(
                "link_voice_call_to_event: no voice_calls row for %s",
                vapi_call_id,
            )
            return False
        log.info(
            "Linked voice_call %s -> calendar_event %d",
            vapi_call_id,
            calendar_event_id,
        )
        return True
    except sqlite3.OperationalError as e:
        log.error("link_voice_call_to_event failed: %s", e)
        return False
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Contact interactions — log the call as an interaction
# ---------------------------------------------------------------------------


def log_contact_interaction(
    db_path: Path,
    *,
    contact_id: int,
    vapi_call_id: str,
    duration_s: int | None,
    outcome: str,
    summary: str | None,
) -> None:
    """Log the voice call as a contact_interactions row."""
    db = get_db(db_path)
    try:
        cols = [r["name"] for r in db.execute("PRAGMA table_info(contact_interactions)").fetchall()]
        if not cols:
            return

        insert_cols = ["contact_id"]
        insert_vals: list[Any] = [contact_id]

        # Required fields per schema: contact_id, type, direction, occurred_at
        # (type CHECK: email|call|meeting|message|other)
        # (direction CHECK: inbound|outbound)
        if "type" in cols:
            insert_cols.append("type")
            insert_vals.append("call")
        if "direction" in cols:
            insert_cols.append("direction")
            insert_vals.append("inbound")
        if "subject" in cols:
            insert_cols.append("subject")
            duration_str = f" ({duration_s}s)" if duration_s else ""
            insert_vals.append(f"Voice call — {outcome}{duration_str}")
        if "summary" in cols:
            insert_cols.append("summary")
            summary_text = summary or f"Voice channel call {vapi_call_id}"
            if duration_s:
                summary_text += f" (duration: {duration_s}s)"
            insert_vals.append(summary_text)
        if "occurred_at" in cols:
            insert_cols.append("occurred_at")
            insert_vals.append(datetime.utcnow().isoformat() + "Z")
        if "created_at" in cols:
            insert_cols.append("created_at")
            insert_vals.append(datetime.utcnow().isoformat() + "Z")

        placeholders = ", ".join(["?"] * len(insert_cols))
        db.execute(
            f"INSERT INTO contact_interactions ({', '.join(insert_cols)}) VALUES ({placeholders})",
            insert_vals,
        )
        db.commit()
        log.info("Logged contact_interaction for contact %d (call %s)", contact_id, vapi_call_id)
    except (sqlite3.OperationalError, sqlite3.IntegrityError) as e:
        log.warning("Could not log contact_interaction: %s", e)
    finally:
        db.close()
