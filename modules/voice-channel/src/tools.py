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
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except ImportError:
    ZoneInfo = None  # type: ignore

try:
    import phonenumbers  # type: ignore
except ImportError:
    phonenumbers = None  # type: ignore

from .vapi_messages import ToolInvocation, ToolResult

log = logging.getLogger("voice-channel.tools")


def _normalize_phone(raw: str | None, default_region: str = "CA") -> str | None:
    """Normalize a phone number to E.164 format.

    Uses phonenumbers library if available (handles all formats reliably:
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
# Tool registry — used by the dispatcher in server.py
# ---------------------------------------------------------------------------

ToolFunction = Callable[[Path, dict[str, Any], str | None, dict[str, Any] | None], ToolResult]

TOOL_REGISTRY: dict[str, ToolFunction] = {
    "get_business_hours": get_business_hours,
    "lookup_caller": lookup_caller,
    # Week 1 day 3+ tools land here:
    # "list_services": list_services,
    # "check_availability": check_availability,
    # "book_appointment": book_appointment,
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
