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
from pathlib import Path
from typing import Any, Callable

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
        day (str): A specific day to query, e.g. "monday" or "mon"

    Returns a ToolResult with a human-readable message the LLM can speak
    directly to the caller, plus structured data for downstream use.
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
    timezone = config.get("timezone") or "America/Toronto"

    # Filtering on a specific day
    requested_day = (args.get("day") or "").strip().lower() if isinstance(args.get("day"), str) else ""

    if requested_day:
        # Normalize day input
        day_key = None
        for k, full in DAY_NAMES.items():
            if requested_day in (k, full.lower()):
                day_key = k
                break
        if not day_key:
            return ToolResult.error(
                f"I didn't recognize '{args.get('day')}' as a day of the week.",
                tool_call_id=tool_call_id,
            )

        slot = hours.get(day_key)
        if slot is None:
            message = f"{business_name} is closed on {DAY_NAMES[day_key]}."
        else:
            open_t, close_t = slot[0], slot[1]
            message = f"On {DAY_NAMES[day_key]}, {business_name} is open from {_humanize_time(open_t)} to {_humanize_time(close_t)}."

        return ToolResult.success(
            message=message,
            data={
                "day": DAY_NAMES[day_key],
                "open": slot[0] if slot else None,
                "close": slot[1] if slot else None,
                "closed": slot is None,
                "timezone": timezone,
            },
            tool_call_id=tool_call_id,
        )

    # Full week summary
    open_days = []
    closed_days = []
    for day_key in DAY_ORDER:
        slot = hours.get(day_key)
        full_name = DAY_NAMES[day_key]
        if slot is None:
            closed_days.append(full_name)
        else:
            open_days.append(f"{full_name} from {_humanize_time(slot[0])} to {_humanize_time(slot[1])}")

    if open_days:
        message = f"{business_name} is open " + ", ".join(open_days)
        if closed_days:
            message += f". Closed on {' and '.join(closed_days)}."
        else:
            message += "."
    else:
        message = f"{business_name} doesn't have business hours configured."

    return ToolResult.success(
        message=message,
        data={
            "business_name": business_name,
            "timezone": timezone,
            "hours": {DAY_NAMES[k]: hours.get(k) for k in DAY_ORDER},
        },
        tool_call_id=tool_call_id,
    )


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
