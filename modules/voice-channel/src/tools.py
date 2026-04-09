"""Tool implementations for the voice channel.

Each tool is a Python function that takes the SoY database path and arguments
from the LLM, queries SoY's data graph, and returns a ToolResult.

The dispatcher in server.py routes tool invocations from Vapi to these
functions based on the tool name.

SAFETY INVARIANT (per CLAUDE.md):
Every tool must return a structured ToolResult with an explicit `status`
field. The assistant's system prompt is told to NEVER confirm a booking
unless the result.status == 'success'. This is the no-hallucinated-bookings
safety rail and it must hold for every tool that mutates state.

v1 implements one tool: get_business_hours.
Week 1 day 3+ adds: list_services, lookup_caller, check_availability,
book_appointment, send_confirmation_sms, transfer_to_human.
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


def get_business_hours(db_path: Path, args: dict[str, Any], tool_call_id: str | None = None) -> ToolResult:
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
# Tool registry — used by the dispatcher in server.py
# ---------------------------------------------------------------------------

ToolFunction = Callable[[Path, dict[str, Any], str | None], ToolResult]

TOOL_REGISTRY: dict[str, ToolFunction] = {
    "get_business_hours": get_business_hours,
    # Week 1 day 3+ tools land here:
    # "list_services": list_services,
    # "lookup_caller": lookup_caller,
    # "check_availability": check_availability,
    # "book_appointment": book_appointment,
    # "send_confirmation_sms": send_confirmation_sms,
    # "transfer_to_human": transfer_to_human,
    # "log_call_outcome": log_call_outcome,
}


def dispatch_tool(db_path: Path, invocation: ToolInvocation) -> ToolResult:
    """Route a tool invocation to its implementation, with error handling."""
    fn = TOOL_REGISTRY.get(invocation.name)
    if fn is None:
        log.warning("Unknown tool requested: %s", invocation.name)
        return ToolResult.error(
            f"Tool '{invocation.name}' is not implemented yet.",
            tool_call_id=invocation.tool_call_id,
        )

    try:
        result = fn(db_path, invocation.arguments, invocation.tool_call_id)
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
