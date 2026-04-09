"""Tool implementations for the voice channel.

Each tool is a Python function that takes the SoY database path, arguments
from the LLM, a tool call ID (for Vapi correlation), and the current call's
metadata (caller phone, callee phone, vapi_call_id), and returns a ToolResult.

The dispatcher in server.py routes tool invocations from Vapi to these
functions based on the tool name and threads call metadata through.

SAFETY INVARIANT (per CLAUDE.md):
Every tool must return a structured ToolResult with an explicit `status`
field. The assistant's system prompt is told to NEVER confirm a booking
unless the result.status == 'success'. This is the no-hallucinated-bookings
safety rail and it must hold for every tool that mutates state.

Implemented tools:
- get_business_hours: read voice_config.business_hours_json (with temporal
  context so the LLM never has to ask what day it is)
- lookup_caller: cross-reference caller phone against v_contact_health
  and return relationship context for personalized greeting

Upcoming:
- list_services, check_availability, book_appointment,
  send_confirmation_sms, transfer_to_human, log_call_outcome
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except ImportError:
    ZoneInfo = None  # type: ignore

from .persistence import is_owner_phone, normalize_phone as _normalize_phone
from .vapi_messages import ToolInvocation, ToolResult

log = logging.getLogger("voice-channel.tools")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_voice_config(db_path: Path) -> dict[str, Any] | None:
    """Load the singleton voice_config row."""
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    try:
        row = db.execute("SELECT * FROM voice_config WHERE id = 1").fetchone()
        return dict(row) if row else None
    finally:
        db.close()


def _now_in_business_tz(config: dict[str, Any]) -> datetime:
    """Return the current time in the business's configured timezone.

    Every time-sensitive tool should use this instead of datetime.now()
    directly — the LLM asks questions like 'are you open today' and
    needs answers grounded in the caller's (= business's) local time,
    not UTC or server-local.
    """
    tz_name = config.get("timezone") or "America/Toronto"
    if ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo(tz_name))
        except Exception:
            pass
    # Fallback: system local time (only happens if zoneinfo unavailable)
    return datetime.now()


def _build_temporal_context(config: dict[str, Any]) -> dict[str, Any]:
    """Build a temporal context dict that every time-sensitive tool can include.

    This is the critical UX fix: tools return "today is Thursday, April 9,
    it's 1:45pm" inside their data so the LLM never has to ask the caller
    "what day is it today?" — it has the info from the tool response
    metadata and can answer naturally.
    """
    now = _now_in_business_tz(config)
    day_key = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][now.weekday()]
    return {
        "today_day_key": day_key,
        "today_day_name": DAY_NAMES[day_key],
        "today_date": now.strftime("%A, %B %-d, %Y"),
        "today_iso": now.date().isoformat(),
        "current_time": now.strftime("%-I:%M %p"),
        "current_time_24h": now.strftime("%H:%M"),
        "timezone": config.get("timezone") or "America/Toronto",
    }


# ---------------------------------------------------------------------------
# Tool: get_business_hours
# ---------------------------------------------------------------------------

DAY_NAMES = {
    "mon": "Monday",
    "tue": "Tuesday",
    "wed": "Wednesday",
    "thu": "Thursday",
    "fri": "Friday",
    "sat": "Saturday",
    "sun": "Sunday",
}

DAY_ORDER = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def get_business_hours(
    db_path: Path,
    args: dict[str, Any],
    tool_call_id: str | None = None,
    call_meta: dict[str, Any] | None = None,
) -> ToolResult:
    """Return the business hours configured in voice_config.

    Args (from LLM, all optional):
        day (str): A specific day to query, e.g. "monday", "today", "tomorrow"

    The response always includes the current day/time in the business's
    timezone, so the LLM never has to ask the caller "what day is it today?".
    When called with no arguments (typical for "are you open?" / "what are
    your hours?"), the message leads with today's status.
    """
    config = _get_voice_config(db_path)
    if not config:
        return ToolResult.error("Business hours are not configured.", tool_call_id=tool_call_id)

    raw = config.get("business_hours_json")
    if not raw:
        return ToolResult.error("Business hours have not been set up yet.", tool_call_id=tool_call_id)

    try:
        hours: dict[str, list[str] | None] = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        log.error("voice_config.business_hours_json is malformed: %r", raw)
        return ToolResult.error("Business hours configuration is invalid.", tool_call_id=tool_call_id)

    business_name = config.get("business_name") or "the business"
    temporal = _build_temporal_context(config)
    today_key = temporal["today_day_key"]

    # --- Resolve the requested day ---
    # Accept: None/empty (default: today + week context), specific day, "today", "tomorrow"
    raw_requested = args.get("day") if isinstance(args.get("day"), str) else None
    requested_day = (raw_requested or "").strip().lower()
    day_key: str | None = None

    if requested_day in ("", "any", "all", "week"):
        day_key = None  # Full week summary
    elif requested_day == "today":
        day_key = today_key
    elif requested_day == "tomorrow":
        idx = DAY_ORDER.index(today_key)
        day_key = DAY_ORDER[(idx + 1) % 7]
    else:
        # Normalize day name input
        for k, full in DAY_NAMES.items():
            if requested_day in (k, full.lower()):
                day_key = k
                break
        if day_key is None:
            return ToolResult.error(
                f"I didn't recognize '{raw_requested}' as a day of the week.",
                tool_call_id=tool_call_id,
            )

    # --- Today's status (always computed, used in message or as data) ---
    today_slot = hours.get(today_key)
    today_is_open = today_slot is not None
    is_open_now = False
    if today_is_open:
        open_t, close_t = today_slot[0], today_slot[1]
        is_open_now = open_t <= temporal["current_time_24h"] < close_t

    today_status_str = _today_status_sentence(
        business_name,
        today_key,
        today_slot,
        is_open_now,
        now_24h=temporal["current_time_24h"],
    )

    # --- Response for a specific day query ---
    if day_key is not None and day_key != today_key:
        slot = hours.get(day_key)
        if slot is None:
            message = f"{business_name} is closed on {DAY_NAMES[day_key]}."
        else:
            message = (
                f"On {DAY_NAMES[day_key]}, {business_name} is open from "
                f"{_humanize_time(slot[0])} to {_humanize_time(slot[1])}."
            )
        # Still include today's context so the LLM can reference it naturally
        message = f"{today_status_str} {message}"

        return ToolResult.success(
            message=message,
            data={
                "queried_day": DAY_NAMES[day_key],
                "open": slot[0] if slot else None,
                "close": slot[1] if slot else None,
                "closed": slot is None,
                **temporal,
                "is_open_now": is_open_now,
            },
            tool_call_id=tool_call_id,
        )

    # --- Response for "today" specifically ---
    if day_key == today_key:
        return ToolResult.success(
            message=today_status_str,
            data={
                "queried_day": temporal["today_day_name"],
                "open": today_slot[0] if today_is_open else None,
                "close": today_slot[1] if today_is_open else None,
                "closed": not today_is_open,
                **temporal,
                "is_open_now": is_open_now,
            },
            tool_call_id=tool_call_id,
        )

    # --- Full week summary (with today leading) ---
    open_days = []
    closed_days = []
    for dk in DAY_ORDER:
        slot = hours.get(dk)
        full_name = DAY_NAMES[dk]
        if slot is None:
            closed_days.append(full_name)
        else:
            open_days.append(f"{full_name} from {_humanize_time(slot[0])} to {_humanize_time(slot[1])}")

    if open_days:
        week_summary = f"For the full week, {business_name} is open " + ", ".join(open_days)
        if closed_days:
            week_summary += f". Closed on {' and '.join(closed_days)}."
        else:
            week_summary += "."
    else:
        week_summary = f"{business_name} doesn't have regular business hours configured."

    # Lead with today's status — this is the UX fix
    message = f"{today_status_str} {week_summary}"

    return ToolResult.success(
        message=message,
        data={
            "business_name": business_name,
            "hours": {DAY_NAMES[k]: hours.get(k) for k in DAY_ORDER},
            "today_open": today_slot[0] if today_is_open else None,
            "today_close": today_slot[1] if today_is_open else None,
            "today_closed": not today_is_open,
            "is_open_now": is_open_now,
            **temporal,
        },
        tool_call_id=tool_call_id,
    )


def _today_status_sentence(
    business_name: str,
    today_key: str,
    today_slot: list[str] | None,
    is_open_now: bool,
    *,
    now_24h: str,
) -> str:
    """Render today's status as a natural sentence the LLM can speak directly.

    Examples:
        "Today is Thursday. Alex Somerville VO is open right now until 6 pm."
        "Today is Thursday. Alex Somerville VO opens at 9 am and closes at 6 pm."  (before open)
        "Today is Thursday. Alex Somerville VO closed for the day at 6 pm."         (after close)
        "Today is Sunday. Alex Somerville VO is closed today."
    """
    day_name = DAY_NAMES[today_key]

    if today_slot is None:
        return f"Today is {day_name}. {business_name} is closed today."

    open_t = today_slot[0]
    close_t = today_slot[1]
    open_h = _humanize_time(open_t)
    close_h = _humanize_time(close_t)

    if is_open_now:
        return f"Today is {day_name}. {business_name} is open right now until {close_h}."

    # Before/after hours determined by comparing "HH:MM" strings in the same tz
    if now_24h < open_t:
        return f"Today is {day_name}. {business_name} opens at {open_h} and closes at {close_h}."
    return f"Today is {day_name}. {business_name} is closed for the day. They closed at {close_h}."


def _humanize_time(t: str) -> str:
    """Convert '14:00' to '2 pm'. Returns the original string if parsing fails."""
    try:
        hour_s, minute_s = t.split(":")
        hour = int(hour_s)
        minute = int(minute_s)
    except (ValueError, AttributeError):
        return t

    suffix = "am" if hour < 12 else "pm"
    display_hour = hour % 12
    if display_hour == 0:
        display_hour = 12

    if minute == 0:
        return f"{display_hour} {suffix}"
    return f"{display_hour}:{minute:02d} {suffix}"


# ---------------------------------------------------------------------------
# Tool: lookup_caller
# ---------------------------------------------------------------------------


def lookup_caller(
    db_path: Path,
    args: dict[str, Any],
    tool_call_id: str | None = None,
    call_meta: dict[str, Any] | None = None,
) -> ToolResult:
    """Identify the caller and return relationship context for personalized greeting.

    The LLM is expected to call this at the very start of every call, typically
    without any arguments — the tool pulls the caller's phone from the current
    call metadata. If the LLM does pass a phone argument (e.g., the user verbally
    gave it), we prefer that.

    Returns:
        - Known contact: name, company, role, relationship depth, trajectory,
          days since last contact, active projects count, open commitments
          counts, recent email count, next scheduled meeting
        - Unknown caller: status=success with `known: false` — the LLM should
          use a generic greeting and maybe offer to add them as a contact

    This tool is the centerpiece of SoY's voice channel differentiation.
    Generic voice agents can't do this because they don't have a unified
    personal data graph underneath.
    """
    # Prefer the phone the LLM passed, fall back to the current call's from_number
    raw_phone = args.get("phone") or (call_meta or {}).get("from_number") or ""
    phone = (raw_phone or "").strip()

    if not phone:
        return ToolResult.error(
            "No caller phone number available to look up.",
            tool_call_id=tool_call_id,
        )

    # Normalize the query phone to E.164 so we can compare against any format
    normalized_query = _normalize_phone(phone) or phone

    # Owner self-call: the SoY operator is calling their own line. Return
    # immediately with an owner_call branch BEFORE touching contacts. This
    # is the only place that knows the caller is the owner — without this
    # check, the lookup would either return "unknown" (first call) or match
    # the auto-created `Caller +1...` placeholder (every subsequent call),
    # neither of which is right.
    if is_owner_phone(db_path, normalized_query):
        config = _get_voice_config(db_path)
        owner_name = (config or {}).get("owner_name") or "the owner"
        business_name = (config or {}).get("business_name") or "the business"
        return ToolResult.success(
            message=(
                f"The caller is {owner_name} — the {business_name} owner — calling from "
                "their own line. This is an owner test or admin call. Greet them by name "
                "and ask what they want to verify or check. Do not treat this like a "
                "regular customer call and do not offer to take a message."
            ),
            data={
                "known": True,
                "owner_call": True,
                "name": owner_name,
                "business_name": business_name,
                "phone": phone,
            },
            tool_call_id=tool_call_id,
        )

    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    try:
        # Try exact match first (fast path for already-E.164 stored numbers)
        contact_row = db.execute(
            "SELECT id FROM contacts WHERE phone = ? AND status = 'active' LIMIT 1",
            (normalized_query,),
        ).fetchone()

        if not contact_row:
            # Slow path: normalize every stored phone in Python and compare to the
            # normalized query. This handles dots, dashes, parens, spaces, missing
            # country codes, and any other format variance.
            #
            # For the initial SoY deployment this is O(N) over active contacts,
            # which is fine up to a few thousand. If/when the CRM grows, we add
            # an indexed `phone_e164` column and maintain it on contact write.
            candidates = db.execute(
                "SELECT id, phone FROM contacts WHERE phone IS NOT NULL AND phone != '' AND status = 'active'"
            ).fetchall()
            for cand in candidates:
                normalized_stored = _normalize_phone(cand["phone"])
                if normalized_stored and normalized_stored == normalized_query:
                    contact_row = {"id": cand["id"]}
                    break

        if not contact_row:
            # Unknown caller — this is useful information for the LLM
            return ToolResult.success(
                message=f"The caller at {phone} is not in the contact database. Use a generic greeting and offer to add them as a contact if they'd like.",
                data={
                    "known": False,
                    "phone": phone,
                },
                tool_call_id=tool_call_id,
            )

        contact_id = contact_row["id"]

        # Auto-created placeholder contacts (from earlier calls) should be
        # treated as "known phone, not yet named" — not a real relationship
        contact_full = db.execute(
            "SELECT name, notes FROM contacts WHERE id = ?",
            (contact_id,),
        ).fetchone()

        is_placeholder = False
        if contact_full:
            notes = (contact_full["notes"] or "").lower()
            name = (contact_full["name"] or "").lower()
            if "auto-created from inbound voice call" in notes or name.startswith("caller +"):
                is_placeholder = True

        if is_placeholder:
            return ToolResult.success(
                message=(
                    f"The caller at {phone} has called before but hasn't introduced themselves yet. "
                    "Ask for their name naturally as part of the conversation."
                ),
                data={
                    "known": True,
                    "placeholder": True,
                    "contact_id": contact_id,
                    "phone": phone,
                },
                tool_call_id=tool_call_id,
            )

        # Full relationship context from the computed view
        ch = db.execute(
            "SELECT * FROM v_contact_health WHERE id = ?",
            (contact_id,),
        ).fetchone()

        if not ch:
            return ToolResult.success(
                message=f"Found contact {contact_full['name']} but no relationship data available.",
                data={
                    "known": True,
                    "contact_id": contact_id,
                    "name": contact_full["name"],
                    "phone": phone,
                },
                tool_call_id=tool_call_id,
            )

        ch_dict = dict(ch)

        # Render a natural context sentence the LLM can use in its greeting
        message_parts = _render_caller_context(ch_dict)
        message = " ".join(message_parts)

        return ToolResult.success(
            message=message,
            data={
                "known": True,
                "placeholder": False,
                "contact_id": ch_dict["id"],
                "name": ch_dict["name"],
                "company": ch_dict.get("company"),
                "role": ch_dict.get("role"),
                "email": ch_dict.get("email"),
                "phone": phone,
                "days_silent": ch_dict.get("days_silent"),
                "emails_30d": ch_dict.get("emails_30d"),
                "interactions_30d": ch_dict.get("interactions_30d"),
                "transcripts_30d": ch_dict.get("transcripts_30d"),
                "active_projects": ch_dict.get("active_projects"),
                "your_open_commitments": ch_dict.get("your_open_commitments"),
                "their_open_commitments": ch_dict.get("their_open_commitments"),
                "overdue_commitments": ch_dict.get("overdue_commitments"),
                "pending_follow_ups": ch_dict.get("pending_follow_ups"),
                "next_meeting": ch_dict.get("next_meeting"),
                "relationship_depth": ch_dict.get("relationship_depth"),
                "trajectory": ch_dict.get("trajectory"),
                "relationship_notes": ch_dict.get("relationship_notes"),
            },
            tool_call_id=tool_call_id,
        )
    finally:
        db.close()


def _render_caller_context(ch: dict[str, Any]) -> list[str]:
    """Render v_contact_health data as natural sentences for the LLM.

    Returns a list of sentence fragments the LLM can use in its greeting.
    Ordered from most important (identity) to supporting context.
    """
    parts: list[str] = []

    # Identity
    name = ch.get("name") or "the caller"
    company = ch.get("company")
    role = ch.get("role")
    if company and role:
        parts.append(f"This call is from {name}, {role} at {company}.")
    elif company:
        parts.append(f"This call is from {name} at {company}.")
    elif role:
        parts.append(f"This call is from {name}, {role}.")
    else:
        parts.append(f"This call is from {name}.")

    # Recency / trajectory
    days_silent = ch.get("days_silent")
    trajectory = ch.get("trajectory")
    if days_silent is not None:
        if days_silent == 0:
            recency = "You were in contact with them earlier today."
        elif days_silent == 1:
            recency = "You were in contact yesterday."
        elif days_silent <= 7:
            recency = f"You last heard from them {days_silent} days ago."
        elif days_silent <= 30:
            recency = f"It's been about {days_silent} days since you last talked."
        else:
            months = days_silent // 30
            recency = f"It's been roughly {months} month{'s' if months != 1 else ''} since you last talked."
        if trajectory and trajectory not in ("—", "", None):
            recency = f"{recency} The relationship trajectory is {trajectory}."
        parts.append(recency)

    # Active collaboration
    active_projects = ch.get("active_projects") or 0
    if active_projects > 0:
        parts.append(
            f"You have {active_projects} active project"
            f"{'s' if active_projects != 1 else ''} with them."
        )

    # Recent activity
    emails_30d = ch.get("emails_30d") or 0
    transcripts_30d = ch.get("transcripts_30d") or 0
    if emails_30d > 0 or transcripts_30d > 0:
        bits = []
        if emails_30d > 0:
            bits.append(f"{emails_30d} email{'s' if emails_30d != 1 else ''}")
        if transcripts_30d > 0:
            bits.append(f"{transcripts_30d} call{'s' if transcripts_30d != 1 else ''}")
        parts.append(f"In the last 30 days you've exchanged {' and '.join(bits)}.")

    # Commitments — these are important because they're often why people call
    your_c = ch.get("your_open_commitments") or 0
    their_c = ch.get("their_open_commitments") or 0
    overdue = ch.get("overdue_commitments") or 0
    if your_c > 0:
        msg = f"You owe them {your_c} open commitment{'s' if your_c != 1 else ''}"
        if overdue > 0:
            msg += f", and {overdue} of your commitments with them are overdue"
        parts.append(msg + ".")
    if their_c > 0:
        parts.append(
            f"They owe you {their_c} open commitment{'s' if their_c != 1 else ''}."
        )

    # Pending follow-ups
    pending_followups = ch.get("pending_follow_ups") or 0
    if pending_followups > 0:
        parts.append(
            f"There are {pending_followups} pending follow-up{'s' if pending_followups != 1 else ''} with them."
        )

    # Next meeting
    next_meeting = ch.get("next_meeting")
    if next_meeting:
        parts.append(f"You have a scheduled meeting coming up on {next_meeting}.")

    # Relationship notes — free text the user has written about this contact
    notes = ch.get("relationship_notes")
    if notes and notes.strip():
        # Truncate to keep prompt efficient
        notes_short = notes.strip()
        if len(notes_short) > 200:
            notes_short = notes_short[:200] + "..."
        parts.append(f"Notes on this contact: {notes_short}")

    return parts


# ---------------------------------------------------------------------------
# Tool: check_availability
# ---------------------------------------------------------------------------


def _parse_natural_date(text: str, timezone: str = "America/Toronto") -> datetime | None:
    """Parse a natural-language date like 'tomorrow' or 'next tuesday' into a datetime.

    Lazy-imports dateparser (it's slow on cold import). Tries the rich path
    first; falls back to plain dateparser without settings (which handles
    'next tuesday' but not 'tuesday' alone consistently); finally falls back
    to manual handling of the simplest cases.

    The 'next ' / 'this ' prefix is stripped before passing to dateparser
    because dateparser's PREFER_DATES_FROM=future setting already interprets
    bare day names as the next occurrence — 'next tuesday' becomes redundant
    and (in dateparser 1.4) returns None when both are combined.
    """
    if not text:
        return None
    text = text.strip().lower()
    if not text:
        return None

    # Strip "next "/"this " prefix — dateparser with PREFER_DATES_FROM=future
    # already returns the next occurrence of a bare day name. Combining the
    # two confuses dateparser 1.4 and returns None.
    if text.startswith("next "):
        text = text[5:].strip()
    elif text.startswith("this "):
        text = text[5:].strip()

    if not text:
        return None

    tz = ZoneInfo(timezone) if ZoneInfo else None
    now = datetime.now(tz) if tz else datetime.now()

    # Try dateparser with rich settings first
    try:
        import dateparser  # type: ignore
        result = dateparser.parse(
            text,
            settings={
                "PREFER_DATES_FROM": "future",
                "TIMEZONE": timezone,
                "RETURN_AS_TIMEZONE_AWARE": True,
                "RELATIVE_BASE": now,
            },
        )
        if result:
            return result
        # Second attempt: no settings (handles edge cases the rich path drops)
        result = dateparser.parse(text)
        if result:
            # Make sure it's timezone-aware
            if result.tzinfo is None and tz is not None:
                result = result.replace(tzinfo=tz)
            return result
    except Exception as e:
        log.warning("dateparser failed for %r: %s", text, e)

    # Fallback for the simplest cases
    if text == "today":
        return now
    if text == "tomorrow":
        return now + timedelta(days=1)
    return None


def _format_slot_humanized(slot_iso: str) -> str:
    """Render an ISO slot start as '9:00 AM'."""
    try:
        dt = datetime.fromisoformat(slot_iso)
        return dt.strftime("%-I:%M %p").lstrip("0")
    except (ValueError, AttributeError):
        return slot_iso


def _sample_representative_slots(slots: list, count: int = 3) -> list:
    """Pick `count` evenly-spaced slots from a list — used to give the LLM a
    natural-feeling sample when the full slot list is too long to recite.

    With count=3 and 8 slots, returns the first, middle, and last slots.
    With count <= 0 or fewer slots than requested, returns the input unchanged.
    """
    if count <= 0 or len(slots) <= count:
        return slots
    n = len(slots)
    indices = sorted({int(round(i * (n - 1) / (count - 1))) for i in range(count)})
    return [slots[i] for i in indices]


def check_availability(
    db_path: Path,
    args: dict[str, Any],
    tool_call_id: str | None = None,
    call_meta: dict[str, Any] | None = None,
) -> ToolResult:
    """Find open appointment slots for a given date.

    The voice agent should call this BEFORE asking the caller to commit
    to a specific time. Returns a list of available slots formatted for
    natural conversation.

    Args (from LLM):
        date (str, required): When the caller wants to book — accepts
            "today", "tomorrow", a day name like "tuesday", or a specific
            date like "2026-04-14".
        time_preference (str, optional): One of "morning", "afternoon",
            "evening" to narrow the slots. Omit for full-day options.

    Implementation:
        - Reads voice_config for business_hours, services_json, timezone
        - Default duration is taken from services_json[0].duration_min,
          falling back to 60 if services_json is missing or malformed
          (multi-service support is a follow-up commit)
        - Calls calendar_backend.find_free_slots() which queries freebusy
          across ALL of Alex's connected calendars (not just primary) so
          the bot never schedules over a meeting on a side calendar
    """
    config = _get_voice_config(db_path)
    if not config:
        return ToolResult.error(
            "Voice channel is not configured.",
            tool_call_id=tool_call_id,
        )

    business_hours_raw = config.get("business_hours_json")
    if not business_hours_raw:
        return ToolResult.error(
            "Business hours have not been set up — can't check availability.",
            tool_call_id=tool_call_id,
        )
    try:
        business_hours = json.loads(business_hours_raw)
    except (json.JSONDecodeError, TypeError):
        log.error("voice_config.business_hours_json is malformed")
        return ToolResult.error(
            "Business hours configuration is invalid.",
            tool_call_id=tool_call_id,
        )

    timezone = config.get("timezone") or "America/Toronto"
    business_name = config.get("business_name") or "the business"

    # Resolve the requested date
    date_arg = args.get("date") or ""
    if not isinstance(date_arg, str) or not date_arg.strip():
        return ToolResult.error(
            "I need a date to check availability for.",
            tool_call_id=tool_call_id,
        )

    target_date = _parse_natural_date(date_arg, timezone=timezone)
    if not target_date:
        return ToolResult.error(
            f"I didn't recognize '{date_arg}' as a date.",
            tool_call_id=tool_call_id,
        )

    # Resolve the duration from voice_config.services_json (first service)
    duration_min = 60  # default
    services_raw = config.get("services_json")
    if services_raw:
        try:
            services = json.loads(services_raw)
            if isinstance(services, list) and services:
                first = services[0]
                if isinstance(first, dict) and isinstance(first.get("duration_min"), int):
                    duration_min = first["duration_min"]
        except (json.JSONDecodeError, TypeError):
            log.warning("voice_config.services_json malformed — using default 60 min")

    # Time preference (optional)
    time_pref = args.get("time_preference")
    if time_pref and not isinstance(time_pref, str):
        time_pref = None

    # Get the calendar backend and query slots
    try:
        from .calendar_backend import get_backend
        backend = get_backend(db_path)
    except Exception as e:
        log.exception("Failed to load calendar backend")
        return ToolResult.error(
            f"I'm having trouble reaching the calendar right now. ({e})",
            tool_call_id=tool_call_id,
        )

    try:
        slots = backend.find_free_slots(
            date=target_date,
            duration_min=duration_min,
            business_hours=business_hours,
            buffer_min=0,
            time_preference=time_pref,
            max_slots=8,
        )
    except Exception as e:
        # Distinguish infrastructure failures from generic exceptions so the
        # LLM gets a clear "service unavailable" message instead of "no slots"
        from .calendar_backend import CalendarBackendError
        if isinstance(e, CalendarBackendError):
            log.error("Calendar backend infrastructure error: %s", e)
            return ToolResult.error(
                "I'm having trouble reaching the calendar right now — the connection "
                "to the calendar service is down. Let me have Alex follow up with you "
                "to confirm a time.",
                tool_call_id=tool_call_id,
            )
        log.exception("find_free_slots raised")
        return ToolResult.error(
            f"I had trouble checking the calendar. ({e})",
            tool_call_id=tool_call_id,
        )

    # Render the response
    target_humanized = target_date.strftime("%A, %B %-d").replace(" 0", " ")
    target_iso_date = target_date.date().isoformat()

    if not slots:
        # Could be: closed that day, fully booked, or outside business hours
        day_keys = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        day_key = day_keys[target_date.weekday()]
        if business_hours.get(day_key) is None:
            message = f"{business_name} is closed on {target_humanized}. Want me to check a different day?"
        else:
            message = (
                f"I don't see any open {duration_min}-minute slots on {target_humanized}. "
                "Want me to check a different day?"
            )
        return ToolResult.success(
            message=message,
            data={
                "requested_date": target_iso_date,
                "requested_date_humanized": target_humanized,
                "duration_min": duration_min,
                "slots": [],
                "time_preference": time_pref,
            },
            tool_call_id=tool_call_id,
        )

    # Choose presentation density based on how full the day is.
    # Reciting 8 slots back to back feels robotic — when the day is wide
    # open, invite the caller to propose a time and let the LLM check it
    # against data.slots. When it's moderate, give a representative
    # sampling. Only when it's tight do we list every option.
    slot_count = len(slots)
    presentation: str

    def _join(strs: list[str]) -> str:
        if len(strs) == 1:
            return strs[0]
        if len(strs) == 2:
            return f"{strs[0]} or {strs[1]}"
        return ", ".join(strs[:-1]) + f", or {strs[-1]}"

    if slot_count >= 7:
        presentation = "wide_open"
        message = (
            f"Looking at {target_humanized}, the day is pretty open. "
            "Do you have a specific time in mind?"
        )
    elif slot_count >= 4:
        presentation = "sampled"
        sampled = _sample_representative_slots(slots, 3)
        sampled_strs = [_format_slot_humanized(s.start_iso) for s in sampled]
        message = (
            f"On {target_humanized}, I have a few openings — "
            f"{_join(sampled_strs)}, or another time if you have a preference."
        )
    else:
        presentation = "list"
        slot_strs = [_format_slot_humanized(s.start_iso) for s in slots]
        message = f"On {target_humanized}, I have {_join(slot_strs)}. Which works?"

    return ToolResult.success(
        message=message,
        data={
            "requested_date": target_iso_date,
            "requested_date_humanized": target_humanized,
            "duration_min": duration_min,
            "time_preference": time_pref,
            "presentation": presentation,
            "total_open_slots": slot_count,
            "slots": [
                {
                    "start_iso": s.start_iso,
                    "end_iso": s.end_iso,
                    "humanized": _format_slot_humanized(s.start_iso),
                    "duration_min": s.duration_min,
                }
                for s in slots
            ],
        },
        tool_call_id=tool_call_id,
    )


# ---------------------------------------------------------------------------
# Tool: book_appointment
# ---------------------------------------------------------------------------


def book_appointment(
    db_path: Path,
    args: dict[str, Any],
    tool_call_id: str | None = None,
    call_meta: dict[str, Any] | None = None,
) -> ToolResult:
    """Book an appointment on the configured calendar.

    SAFETY INVARIANT: This is the load-bearing tool for the no-hallucinated-
    bookings rail. The voice agent's system prompt is told NEVER to confirm
    a booking unless this tool returns status='success'. If create_event
    fails, we return ToolResult.error and the agent must say "I'm having
    trouble reaching the calendar, let me have Alex call you back" rather
    than fabricating a confirmation.

    Sequence:
        1. Validate inputs (date, time, caller_name required)
        2. calendar_backend.create_event() — REAL Google Calendar event lands
        3. mirror_calendar_event() — local calendar_events row written
        4. link_voice_call_to_event() — voice_calls.booked_event_id wired
        5. messaging_backend.send_sms() — confirmation text to caller
        6. notify_owner() — telegram ping to Alex
        7. Return success

    Each downstream step's failure (3-6) is logged separately to voice_events
    so we can tell exactly where it broke. Steps 3-6 do not roll back the
    Google event — the booking is real even if SMS or telegram fail.

    Args (from LLM):
        date (str, required): YYYY-MM-DD
        time (str, required): HH:MM in 24-hour format
        caller_name (str, required)
        caller_phone (str, optional): Defaults to call_meta.from_number
        notes (str, optional)
    """
    # --- Input validation ---
    date_str = args.get("date") or ""
    time_str = args.get("time") or ""
    caller_name = (args.get("caller_name") or "").strip()
    caller_phone = (args.get("caller_phone") or (call_meta or {}).get("from_number") or "").strip()
    notes = (args.get("notes") or "").strip()

    if not date_str or not time_str or not caller_name:
        return ToolResult.error(
            "I need the date, time, and caller name to book.",
            tool_call_id=tool_call_id,
        )

    # --- Load voice_config ---
    config = _get_voice_config(db_path)
    if not config:
        return ToolResult.error(
            "Voice channel is not configured.",
            tool_call_id=tool_call_id,
        )
    timezone = config.get("timezone") or "America/Toronto"
    business_name = config.get("business_name") or "the business"
    owner_name = config.get("owner_name") or "the owner"

    # --- Resolve duration ---
    duration_min = 60
    services_raw = config.get("services_json")
    if services_raw:
        try:
            services = json.loads(services_raw)
            if isinstance(services, list) and services:
                first = services[0]
                if isinstance(first, dict) and isinstance(first.get("duration_min"), int):
                    duration_min = first["duration_min"]
        except (json.JSONDecodeError, TypeError):
            pass

    # --- Build the slot ---
    try:
        date_part = datetime.strptime(date_str, "%Y-%m-%d").date()
        hh, mm = (int(x) for x in time_str.split(":"))
        if ZoneInfo is not None:
            tz = ZoneInfo(timezone)
            start_dt = datetime(date_part.year, date_part.month, date_part.day, hh, mm, tzinfo=tz)
        else:
            start_dt = datetime(date_part.year, date_part.month, date_part.day, hh, mm)
    except (ValueError, TypeError) as e:
        return ToolResult.error(
            f"I couldn't parse the date or time: {e}",
            tool_call_id=tool_call_id,
        )
    end_dt = start_dt + timedelta(minutes=duration_min)

    # --- Get the calendar backend ---
    try:
        from .calendar_backend import Slot, get_backend
        backend = get_backend(db_path)
    except Exception as e:
        log.exception("Failed to load calendar backend for booking")
        return ToolResult.error(
            f"I'm having trouble reaching the calendar right now. ({e})",
            tool_call_id=tool_call_id,
        )

    slot = Slot(
        start_iso=start_dt.isoformat(),
        end_iso=end_dt.isoformat(),
        duration_min=duration_min,
        calendar_id=getattr(backend, "calendar_id", "primary"),
    )

    # --- Build event body ---
    event_summary = f"{caller_name} — voice booking"
    description_parts = [
        f"Booked via voice channel.",
        f"Caller: {caller_name}",
    ]
    if caller_phone:
        description_parts.append(f"Phone: {caller_phone}")
    if notes:
        description_parts.append(f"Notes: {notes}")
    description = "\n".join(description_parts)

    # --- Step 2: Create the Google Calendar event (REAL booking) ---
    booking = backend.create_event(
        slot=slot,
        summary=event_summary,
        description=description,
        attendees=None,  # Not asking for caller email in v1 — SMS is the lowest barrier
    )

    if booking.status != "success":
        # SAFETY INVARIANT: hard failure path. The agent MUST NOT confirm.
        return ToolResult.error(
            f"I couldn't reach the calendar to book that. ({booking.error or 'unknown error'}). "
            "Let me have someone call you back.",
            tool_call_id=tool_call_id,
        )

    log.info("Booking created: event_id=%s for %s at %s", booking.event_id, caller_name, slot.start_iso)

    # --- Step 3: Mirror to local calendar_events ---
    local_event_id: int | None = None
    try:
        from .persistence import (
            link_voice_call_to_event,
            mirror_calendar_event,
        )
        # The raw Google event response is preserved in booking.raw
        # We need account_email — pull from the backend if available
        account_email = getattr(backend, "account_email", None)
        local_event_id = mirror_calendar_event(
            db_path,
            google_event_data=booking.raw,
            account_email=account_email,
        )
    except Exception:
        log.exception("Failed to mirror calendar event locally — booking still real in Google")

    # --- Step 4: Link voice_call to the booked event ---
    vapi_call_id = (call_meta or {}).get("vapi_call_id")
    if vapi_call_id and local_event_id:
        try:
            link_voice_call_to_event(db_path, vapi_call_id, local_event_id)
        except Exception:
            log.exception("Failed to link voice_call to calendar_event")

    # --- Step 5: Send SMS confirmation to caller ---
    sms_status = "skipped"
    if caller_phone:
        try:
            from .messaging_backend import get_messaging_backend
            messaging = get_messaging_backend()
            slot_humanized = _format_slot_humanized(slot.start_iso)
            sms_body = (
                f"Hi {caller_name.split()[0]}, you're booked with {business_name} "
                f"on {start_dt.strftime('%A, %B %-d').replace(' 0', ' ')} at {slot_humanized}. "
                f"Reply to this text if you need to change anything. — {owner_name}"
            )
            send_result = messaging.send_sms(caller_phone, sms_body)
            sms_status = send_result.status
            if send_result.status == "logged":
                log.info("SMS confirmation logged (LogOnly fallback) — Twilio not yet configured")
            elif send_result.status != "success":
                log.warning("SMS confirmation failed: %s", send_result.error or "unknown")
        except Exception:
            log.exception("Messaging backend raised — SMS not sent (booking still real)")
            sms_status = "error"

    # --- Step 6: Notify owner via Telegram ---
    try:
        from .notify import notify_owner
        nice_date = start_dt.strftime("%A, %B %-d").replace(" 0", " ")
        nice_time = _format_slot_humanized(slot.start_iso)
        body_lines = [
            f"{caller_name} booked a {duration_min}-min slot",
            f"📅 {nice_date} at {nice_time}",
            f"📞 {caller_phone or '(no phone)'}",
        ]
        if notes:
            body_lines.append(f"📝 {notes}")
        if sms_status == "logged":
            body_lines.append("⚠️ SMS confirmation NOT sent (Twilio env vars unset)")
        if booking.event_url:
            body_lines.append("")
            body_lines.append(f"View: {booking.event_url}")
        notify_owner(
            db_path,
            subject="📅 New voice booking",
            body="\n".join(body_lines),
            channels=["telegram"],
        )
    except Exception:
        log.exception("notify_owner raised — telegram skipped")

    # --- Step 7: Success response to LLM ---
    confirmation_phrase = (
        f"You're booked, {caller_name.split()[0]}. "
        f"{start_dt.strftime('%A, %B %-d').replace(' 0', ' ')} at {_format_slot_humanized(slot.start_iso)}."
    )
    # SMS confirmation language must match what actually happened — never
    # claim "I just texted you" if no SMS actually went out (LogOnlyBackend
    # returns 'logged' specifically so we can differentiate). Telling the
    # caller a verifiable falsehood is the same class of safety bug as
    # confirming an unverified booking.
    if sms_status == "success":
        confirmation_phrase += " I just texted you a confirmation."
    elif sms_status == "logged":
        # SMS pipeline isn't wired (env vars unset) — booking is real,
        # just be honest that the text isn't actually going out yet
        confirmation_phrase += " Alex will text you the details shortly."
    elif caller_phone and sms_status != "skipped":
        # SMS failed but the booking is real — don't lie to the caller
        confirmation_phrase += " I'll have Alex follow up with the details."

    return ToolResult.success(
        message=confirmation_phrase,
        data={
            "event_id": booking.event_id,
            "event_url": booking.event_url,
            "calendar_id": booking.calendar_id,
            "start_iso": slot.start_iso,
            "end_iso": slot.end_iso,
            "duration_min": duration_min,
            "caller_name": caller_name,
            "caller_phone": caller_phone or None,
            "sms_status": sms_status,
            "local_event_id": local_event_id,
        },
        tool_call_id=tool_call_id,
    )


# ---------------------------------------------------------------------------
# Tool registry — used by the dispatcher in server.py
# ---------------------------------------------------------------------------

ToolFunction = Callable[[Path, dict[str, Any], str | None, dict[str, Any] | None], ToolResult]

TOOL_REGISTRY: dict[str, ToolFunction] = {
    "get_business_hours": get_business_hours,
    "lookup_caller": lookup_caller,
    "check_availability": check_availability,
    "book_appointment": book_appointment,
    # Future tools (Week 2+):
    # "list_services": list_services,
    # "send_confirmation_sms": send_confirmation_sms,
    # "transfer_to_human": transfer_to_human,
    # "log_call_outcome": log_call_outcome,
}


def dispatch_tool(
    db_path: Path,
    invocation: ToolInvocation,
    call_meta: dict[str, Any] | None = None,
) -> ToolResult:
    """Route a tool invocation to its implementation, with error handling.

    Args:
        db_path: Path to the SoY database
        invocation: Parsed tool invocation from the Vapi webhook
        call_meta: Current call metadata (from_number, to_number, vapi_call_id,
                   assistant_id) so tools can access caller context without
                   requiring the LLM to pass it as an argument every time
    """
    fn = TOOL_REGISTRY.get(invocation.name)
    if fn is None:
        log.warning("Unknown tool requested: %s", invocation.name)
        return ToolResult.error(
            f"Tool '{invocation.name}' is not implemented yet.",
            tool_call_id=invocation.tool_call_id,
        )

    try:
        result = fn(db_path, invocation.arguments, invocation.tool_call_id, call_meta)
        log.info(
            "Tool %s -> status=%s msg=%s",
            invocation.name,
            result.status,
            result.message[:80],
        )
        return result
    except Exception as e:
        log.exception("Tool %s raised an exception", invocation.name)
        return ToolResult.error(
            f"Tool '{invocation.name}' failed: {e}",
            tool_call_id=invocation.tool_call_id,
        )
