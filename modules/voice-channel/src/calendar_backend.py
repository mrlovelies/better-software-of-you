"""Calendar backend abstraction for the voice channel.

This module is the boundary between voice-channel's tools and any specific
calendar provider (Google, CalDAV, Cal.com, etc.). The voice-channel
booking flow calls into the abstraction; only the backend implementation
knows how to talk to a particular calendar service.

Why this exists:
    1. Future tenants will use different calendar providers — this keeps
       voice-channel from getting pidgeon-holed into Google.
    2. Slot-generation logic (business hours, buffers, free-windows) is
       provider-agnostic and lives in pure functions that are easy to
       unit-test.
    3. Each backend handles credentials/auth its own way, hidden from
       the booking tool which just calls the protocol methods.

Backends in v1:
    - GoogleCalendarBackend: uses freebusy.query + events.insert against
      ALL of the user's connected calendars (not just primary) so the bot
      never schedules over a meeting on a side calendar.

Future backends (deferred):
    - CalDAVBackend (Nextcloud, Apple Calendar)
    - CalcomBackend (Cal.com has a real API)
    - LocalSqliteBackend (no external calendar — write only to SoY)

The Google API HTTP plumbing lives in shared/google_sync.py (technically
mcp-server/src/software_of_you/google_sync.py) and is imported via a
sys.path injection so we don't have to vendor it. Voice-channel still
runs in its own venv, has its own systemd unit, and never shares process
state with the rest of SoY — this is a code-reuse import only, not a
runtime coupling.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except ImportError:
    ZoneInfo = None  # type: ignore

# --- Bridge to shared/google_sync.py without vendoring ----------------------
# voice-channel/src/calendar_backend.py is at:
#   .../<repo>/modules/voice-channel/src/calendar_backend.py
# software_of_you lives at:
#   .../<repo>/mcp-server/src/software_of_you/...
# parents[3] of __file__ = the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_MCP_SRC = _REPO_ROOT / "mcp-server" / "src"
if _MCP_SRC.exists() and str(_MCP_SRC) not in sys.path:
    sys.path.insert(0, str(_MCP_SRC))

try:
    from software_of_you.google_auth import get_valid_token, list_accounts  # type: ignore
    from software_of_you.google_sync import (  # type: ignore
        cancel_calendar_event,
        create_calendar_event,
        freebusy_query,
        get_calendar_event,
        list_calendars,
    )
    GOOGLE_AVAILABLE = True
except Exception as e:  # pragma: no cover
    GOOGLE_AVAILABLE = False
    _GOOGLE_IMPORT_ERROR = str(e)

log = logging.getLogger("voice-channel.calendar_backend")


class CalendarBackendError(Exception):
    """Raised when a calendar backend can't complete a query for infrastructure
    reasons (token expired, API down, etc).

    This is distinct from "no slots available" — which is a legitimate empty
    result, not an error. The booking flow needs to differentiate so it can
    say "I can't reach the calendar right now" instead of "you're fully booked"
    when the underlying problem is auth or networking. Silent fall-through
    on infra failures is a load-bearing safety bug — it makes the bot lie
    about availability.
    """


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Slot:
    """A bookable time slot.

    All datetime fields are stored as ISO 8601 strings (with timezone offset)
    so they round-trip cleanly through JSON for tool responses and DB writes.
    """
    start_iso: str
    end_iso: str
    duration_min: int
    calendar_id: str

    @property
    def start_dt(self) -> datetime:
        return _parse_iso(self.start_iso)

    @property
    def end_dt(self) -> datetime:
        return _parse_iso(self.end_iso)

    def humanized(self) -> str:
        """Render as a natural-language time, e.g. '2:00 PM' or '9:30 AM'."""
        dt = self.start_dt
        return dt.strftime("%-I:%M %p").lstrip("0")


@dataclass
class BookingResult:
    """The structured result of an attempted booking.

    Mirrors the safety-invariant pattern from ToolResult: every booking
    operation returns one of these with an explicit status, so the LLM
    can never confuse 'in progress' or 'unknown' with 'success'.
    """
    status: str  # "success" | "error"
    event_id: str | None = None
    event_url: str | None = None
    calendar_id: str | None = None
    start_iso: str | None = None
    end_iso: str | None = None
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(
        cls,
        event_id: str,
        event_url: str | None,
        calendar_id: str,
        start_iso: str,
        end_iso: str,
        raw: dict[str, Any] | None = None,
    ) -> "BookingResult":
        return cls(
            status="success",
            event_id=event_id,
            event_url=event_url,
            calendar_id=calendar_id,
            start_iso=start_iso,
            end_iso=end_iso,
            raw=raw or {},
        )

    @classmethod
    def fail(cls, message: str, raw: dict[str, Any] | None = None) -> "BookingResult":
        return cls(status="error", error=message, raw=raw or {})


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class CalendarBackend(Protocol):
    """The interface every calendar provider must implement.

    Implementations are responsible for:
    - Knowing how to talk to their underlying provider (auth, HTTP, etc.)
    - Honoring the business_hours dict and buffer_min when generating slots
    - Returning timezone-aware datetimes in the business's configured tz

    Implementations are NOT responsible for:
    - Mirroring events into SoY's local calendar_events table — that's
      done by the caller after a successful create_event
    - Voice/messaging concerns — those are separate backends
    """

    def find_free_slots(
        self,
        date: datetime,
        duration_min: int,
        business_hours: dict[str, list[str] | None],
        buffer_min: int = 0,
        time_preference: str | None = None,
        max_slots: int = 8,
    ) -> list[Slot]: ...

    def create_event(
        self,
        slot: Slot,
        summary: str,
        description: str | None = None,
        attendees: list[dict[str, str]] | None = None,
    ) -> BookingResult: ...

    def verify_event_exists(self, event_id: str) -> bool: ...

    def cancel_event(self, event_id: str) -> bool: ...


# ---------------------------------------------------------------------------
# Pure helpers (no I/O — easy to unit-test)
# ---------------------------------------------------------------------------


def _parse_iso(s: str) -> datetime:
    """Parse an ISO 8601 timestamp, handling the 'Z' UTC suffix."""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def subtract_busy_blocks(
    window: tuple[datetime, datetime],
    busy_blocks: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    """Subtract busy blocks from a window, returning free intervals.

    Busy blocks are sorted and merged before subtraction so overlapping
    events from different calendars don't double-count.

    Pure function — no I/O. Heavily tested in test_calendar_backend.py.

    Args:
        window: (start, end) of the time range under consideration
        busy_blocks: list of (start, end) busy intervals

    Returns:
        List of (start, end) free intervals, sorted by start time.
        Empty list if the entire window is busy.
    """
    window_start, window_end = window
    if window_start >= window_end:
        return []
    if not busy_blocks:
        return [(window_start, window_end)]

    # Clamp blocks to the window and drop ones outside it
    clamped = []
    for bs, be in busy_blocks:
        if be <= window_start or bs >= window_end:
            continue
        clamped.append((max(bs, window_start), min(be, window_end)))

    if not clamped:
        return [(window_start, window_end)]

    # Sort and merge overlapping busy blocks
    clamped.sort(key=lambda b: b[0])
    merged = [clamped[0]]
    for bs, be in clamped[1:]:
        last_start, last_end = merged[-1]
        if bs <= last_end:
            merged[-1] = (last_start, max(last_end, be))
        else:
            merged.append((bs, be))

    # Walk through merged busy blocks, emitting the gaps as free intervals
    free: list[tuple[datetime, datetime]] = []
    cursor = window_start
    for bs, be in merged:
        if bs > cursor:
            free.append((cursor, bs))
        cursor = max(cursor, be)
    if cursor < window_end:
        free.append((cursor, window_end))

    return free


def generate_slots_in_window(
    window: tuple[datetime, datetime],
    duration_min: int,
    interval_min: int = 30,
) -> list[tuple[datetime, datetime]]:
    """Generate candidate slots inside a free window.

    Slots start every `interval_min` minutes from the window start. A slot
    is only included if it fits entirely inside the window — slots that
    would extend past `window_end` are dropped.

    Pure function — no I/O.

    Args:
        window: (start, end) of the free window
        duration_min: how long each slot needs to be
        interval_min: how often to start a new candidate slot (15, 30, 60)
    """
    window_start, window_end = window
    if duration_min <= 0 or interval_min <= 0:
        return []
    if window_start >= window_end:
        return []

    slots: list[tuple[datetime, datetime]] = []
    cursor = window_start
    duration = timedelta(minutes=duration_min)
    step = timedelta(minutes=interval_min)
    while cursor + duration <= window_end:
        slots.append((cursor, cursor + duration))
        cursor += step
    return slots


def filter_by_time_preference(
    slots: list[tuple[datetime, datetime]],
    time_preference: str | None,
) -> list[tuple[datetime, datetime]]:
    """Filter slots by morning/afternoon/evening preference.

    Boundaries:
    - morning:   start hour < 12
    - afternoon: 12 <= start hour < 17
    - evening:   start hour >= 17

    Pure function. Returns the input unchanged if time_preference is None
    or not one of the recognized values.
    """
    if not time_preference:
        return slots
    pref = time_preference.lower()
    if pref == "morning":
        return [s for s in slots if s[0].hour < 12]
    if pref == "afternoon":
        return [s for s in slots if 12 <= s[0].hour < 17]
    if pref == "evening":
        return [s for s in slots if s[0].hour >= 17]
    return slots


def compute_business_window(
    date: datetime,
    business_hours: dict[str, list[str] | None],
    timezone: str = "America/Toronto",
) -> tuple[datetime, datetime] | None:
    """Compute the business window (open, close) for a given date.

    Args:
        date: Any datetime on the day in question (only the date portion is used)
        business_hours: Dict mapping day keys (mon/tue/.../sun) to ["HH:MM", "HH:MM"]
                        lists, or None for closed days
        timezone: IANA timezone string

    Returns:
        Tuple of timezone-aware (open_dt, close_dt) datetimes, or None if
        the business is closed that day or the hours are invalid.

    Pure function modulo zoneinfo.
    """
    day_keys = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    day_key = day_keys[date.weekday()]
    hours = business_hours.get(day_key)
    if hours is None:
        return None
    if not isinstance(hours, list) or len(hours) != 2:
        return None

    open_str, close_str = hours[0], hours[1]
    try:
        open_h, open_m = (int(x) for x in open_str.split(":"))
        close_h, close_m = (int(x) for x in close_str.split(":"))
    except (ValueError, AttributeError):
        return None

    if ZoneInfo is not None:
        try:
            tz = ZoneInfo(timezone)
        except Exception:
            tz = None
    else:
        tz = None

    open_dt = datetime(date.year, date.month, date.day, open_h, open_m, tzinfo=tz)
    close_dt = datetime(date.year, date.month, date.day, close_h, close_m, tzinfo=tz)

    if open_dt >= close_dt:
        return None
    return (open_dt, close_dt)


def find_free_slots_pure(
    business_window: tuple[datetime, datetime],
    busy_blocks: list[tuple[datetime, datetime]],
    duration_min: int,
    buffer_min: int = 0,
    interval_min: int = 30,
    time_preference: str | None = None,
    max_slots: int = 8,
) -> list[tuple[datetime, datetime]]:
    """The pure version of find_free_slots — no I/O, fully testable.

    Composes the business-window, buffer-padded busy subtraction, slot
    generation, and time-preference filtering into a single function.

    Args:
        business_window: (open, close) of the business day
        busy_blocks: List of (start, end) busy intervals from freebusy
        duration_min: Required slot duration
        buffer_min: Padding around each busy block (and between back-to-back
                    appointments — implemented by padding busy blocks)
        interval_min: Slot start cadence
        time_preference: Optional 'morning'/'afternoon'/'evening' filter
        max_slots: Cap on returned slots (the LLM only needs a few options)

    Returns:
        List of (start, end) tuples, capped at max_slots.
    """
    # Pad busy blocks with buffer to enforce gaps before and after
    padded = []
    for bs, be in busy_blocks:
        if buffer_min > 0:
            padded.append((bs - timedelta(minutes=buffer_min), be + timedelta(minutes=buffer_min)))
        else:
            padded.append((bs, be))

    free_windows = subtract_busy_blocks(business_window, padded)

    all_slots: list[tuple[datetime, datetime]] = []
    for w in free_windows:
        all_slots.extend(generate_slots_in_window(w, duration_min, interval_min))

    filtered = filter_by_time_preference(all_slots, time_preference)
    return filtered[:max_slots]


# ---------------------------------------------------------------------------
# GoogleCalendarBackend
# ---------------------------------------------------------------------------


class GoogleCalendarBackend:
    """CalendarBackend implementation for Google Calendar.

    Uses freebusy.query across ALL the user's connected calendars (not just
    primary) when finding free slots, so the voice agent never schedules
    over a meeting that lives on a side calendar (work, family, etc.).

    Creates events on the configured `calendar_id` (default: "primary").
    Future config: per-tenant booking calendar so a separate "Bookings"
    calendar can be the target without changing code.
    """

    def __init__(
        self,
        account_email: str | None = None,
        calendar_id: str = "primary",
        timezone: str = "America/Toronto",
    ) -> None:
        if not GOOGLE_AVAILABLE:
            raise RuntimeError(
                f"GoogleCalendarBackend requires the software_of_you package "
                f"to be importable. Import error: {_GOOGLE_IMPORT_ERROR}"
            )
        self.account_email = account_email
        self.calendar_id = calendar_id
        self.timezone = timezone

    def _get_token(self) -> str | None:
        return get_valid_token(email=self.account_email)

    def _get_busy_blocks(
        self,
        token: str,
        time_min: datetime,
        time_max: datetime,
    ) -> list[tuple[datetime, datetime]]:
        """Query freebusy across all the user's calendars and flatten the result.

        Raises CalendarBackendError on freebusy API failures (token issue,
        network, etc.) so the caller can distinguish "no busy blocks" from
        "couldn't fetch busy blocks". Silent empty-list-on-failure is a
        safety bug — it makes the bot claim availability it hasn't verified.
        """
        cals = list_calendars(token=token)
        # Honor 'selected' (visible in the user's calendar UI) — these are
        # the calendars that count for "am I free?"
        cal_ids = [
            c["id"]
            for c in cals
            if c.get("selected", False) or c.get("primary", False)
        ]
        if not cal_ids:
            cal_ids = ["primary"]

        fb = freebusy_query(
            token=token,
            calendar_ids=cal_ids,
            time_min=time_min.isoformat(),
            time_max=time_max.isoformat(),
            time_zone=self.timezone,
        )
        if "error" in fb:
            log.error("freebusy_query failed: %s", fb["error"])
            raise CalendarBackendError(
                f"Calendar freebusy query failed: {fb['error']}"
            )

        all_busy: list[tuple[datetime, datetime]] = []
        for _cid, cal_data in fb.get("calendars", {}).items():
            # Per-calendar errors come back inside the calendars dict
            if "errors" in cal_data:
                err_msgs = [e.get("reason", "unknown") for e in cal_data["errors"]]
                log.warning("freebusy per-calendar errors: %s", err_msgs)
            for block in cal_data.get("busy", []):
                try:
                    bs = _parse_iso(block["start"])
                    be = _parse_iso(block["end"])
                    all_busy.append((bs, be))
                except (KeyError, ValueError) as e:
                    log.warning("Skipping malformed busy block %s: %s", block, e)
                    continue
        return all_busy

    def find_free_slots(
        self,
        date: datetime,
        duration_min: int,
        business_hours: dict[str, list[str] | None],
        buffer_min: int = 0,
        time_preference: str | None = None,
        max_slots: int = 8,
    ) -> list[Slot]:
        """Find open slots on the given date.

        Returns an empty list ONLY when the business is closed that day or
        when the calendar is genuinely fully booked. Infrastructure failures
        (token expired, API down) raise CalendarBackendError so the caller
        can surface a clear "calendar unavailable" message rather than
        misrepresenting them as "no availability".
        """
        window = compute_business_window(date, business_hours, self.timezone)
        if window is None:
            return []  # Business is closed that day — legitimate empty result

        token = self._get_token()
        if not token:
            log.error("Cannot find slots — no Google token available for %s", self.account_email)
            raise CalendarBackendError(
                "Google Calendar is not connected (token missing or expired). "
                "The owner needs to re-run /google-setup."
            )

        busy_blocks = self._get_busy_blocks(token, window[0], window[1])

        slot_tuples = find_free_slots_pure(
            business_window=window,
            busy_blocks=busy_blocks,
            duration_min=duration_min,
            buffer_min=buffer_min,
            interval_min=30,
            time_preference=time_preference,
            max_slots=max_slots,
        )

        return [
            Slot(
                start_iso=s[0].isoformat(),
                end_iso=s[1].isoformat(),
                duration_min=duration_min,
                calendar_id=self.calendar_id,
            )
            for s in slot_tuples
        ]

    def create_event(
        self,
        slot: Slot,
        summary: str,
        description: str | None = None,
        attendees: list[dict[str, str]] | None = None,
    ) -> BookingResult:
        token = self._get_token()
        if not token:
            return BookingResult.fail("No Google token available — owner needs to reconnect.")

        event_data: dict[str, Any] = {
            "summary": summary,
            "start": {
                "dateTime": slot.start_iso,
                "timeZone": self.timezone,
            },
            "end": {
                "dateTime": slot.end_iso,
                "timeZone": self.timezone,
            },
        }
        if description:
            event_data["description"] = description
        if attendees:
            event_data["attendees"] = attendees

        response = create_calendar_event(token, self.calendar_id, event_data)
        if "error" in response:
            return BookingResult.fail(response["error"], raw=response)

        event_id = response.get("id")
        if not event_id:
            return BookingResult.fail("Google API returned no event id", raw=response)

        return BookingResult.ok(
            event_id=event_id,
            event_url=response.get("htmlLink"),
            calendar_id=self.calendar_id,
            start_iso=slot.start_iso,
            end_iso=slot.end_iso,
            raw=response,
        )

    def verify_event_exists(self, event_id: str) -> bool:
        token = self._get_token()
        if not token:
            return False
        ev = get_calendar_event(token, self.calendar_id, event_id)
        return ev is not None and ev.get("status") != "cancelled"

    def cancel_event(self, event_id: str) -> bool:
        token = self._get_token()
        if not token:
            return False
        result = cancel_calendar_event(token, self.calendar_id, event_id)
        return bool(result.get("deleted"))


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def _load_voice_config(db_path: Path) -> dict[str, Any]:
    """Load the singleton voice_config row as a plain dict."""
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    try:
        row = db.execute("SELECT * FROM voice_config WHERE id = 1").fetchone()
        return dict(row) if row else {}
    finally:
        db.close()


def get_backend(db_path: Path) -> CalendarBackend:
    """Return the configured calendar backend.

    v1 only supports Google. The first connected Google account is used as
    the source of truth (Alex's preference: default account, will become a
    config field later). Calendar id defaults to "primary".

    Raises RuntimeError if no Google account is connected — voice-channel
    can't book without a calendar provider.
    """
    config = _load_voice_config(db_path)
    timezone = config.get("timezone") or "America/Toronto"

    if not GOOGLE_AVAILABLE:
        raise RuntimeError(
            f"No calendar backend available. Google import failed: {_GOOGLE_IMPORT_ERROR}"
        )

    accounts = list_accounts()
    active = [a for a in accounts if a.get("status") == "active"]
    if not active:
        raise RuntimeError(
            "No active Google account connected. Run `/connect-google` first."
        )

    return GoogleCalendarBackend(
        account_email=active[0]["email"],
        calendar_id="primary",
        timezone=timezone,
    )
