"""Unit tests for the pure helpers in calendar_backend.

These cover the slot-generation logic that the GoogleCalendarBackend
composes — without hitting any real Google API. The pure functions are
where the edge cases live (business hours, buffers, multi-calendar
busy merging, time-of-day filtering); the I/O wrapper just glues them
to freebusy_query and create_calendar_event.

Run with:
    cd modules/voice-channel
    python3 -m pytest tests/test_calendar_backend.py -v

Or run directly:
    python3 modules/voice-channel/tests/test_calendar_backend.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Make src/ importable when running this file from anywhere
_SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from calendar_backend import (  # noqa: E402
    compute_business_window,
    filter_by_time_preference,
    find_free_slots_pure,
    generate_slots_in_window,
    subtract_busy_blocks,
)

# Use a fixed timezone for deterministic test output
TZ = timezone(timedelta(hours=-4))  # America/Toronto in EDT (DST active)


def _dt(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=TZ)


# ---------------------------------------------------------------------------
# subtract_busy_blocks
# ---------------------------------------------------------------------------


def test_subtract_no_busy_blocks_returns_full_window():
    window = (_dt(2026, 4, 14, 9), _dt(2026, 4, 14, 17))
    assert subtract_busy_blocks(window, []) == [window]


def test_subtract_busy_in_middle_splits_window():
    window = (_dt(2026, 4, 14, 9), _dt(2026, 4, 14, 17))
    busy = [(_dt(2026, 4, 14, 12), _dt(2026, 4, 14, 13))]
    result = subtract_busy_blocks(window, busy)
    assert result == [
        (_dt(2026, 4, 14, 9), _dt(2026, 4, 14, 12)),
        (_dt(2026, 4, 14, 13), _dt(2026, 4, 14, 17)),
    ]


def test_subtract_busy_at_start_clips_open():
    window = (_dt(2026, 4, 14, 9), _dt(2026, 4, 14, 17))
    busy = [(_dt(2026, 4, 14, 9), _dt(2026, 4, 14, 10))]
    assert subtract_busy_blocks(window, busy) == [
        (_dt(2026, 4, 14, 10), _dt(2026, 4, 14, 17))
    ]


def test_subtract_busy_at_end_clips_close():
    window = (_dt(2026, 4, 14, 9), _dt(2026, 4, 14, 17))
    busy = [(_dt(2026, 4, 14, 16), _dt(2026, 4, 14, 17))]
    assert subtract_busy_blocks(window, busy) == [
        (_dt(2026, 4, 14, 9), _dt(2026, 4, 14, 16))
    ]


def test_subtract_busy_covers_entire_window_returns_empty():
    window = (_dt(2026, 4, 14, 9), _dt(2026, 4, 14, 17))
    busy = [(_dt(2026, 4, 14, 8), _dt(2026, 4, 14, 18))]
    assert subtract_busy_blocks(window, busy) == []


def test_subtract_overlapping_busy_blocks_are_merged():
    window = (_dt(2026, 4, 14, 9), _dt(2026, 4, 14, 17))
    busy = [
        (_dt(2026, 4, 14, 11), _dt(2026, 4, 14, 13)),
        (_dt(2026, 4, 14, 12), _dt(2026, 4, 14, 14)),  # overlaps the first
    ]
    result = subtract_busy_blocks(window, busy)
    assert result == [
        (_dt(2026, 4, 14, 9), _dt(2026, 4, 14, 11)),
        (_dt(2026, 4, 14, 14), _dt(2026, 4, 14, 17)),
    ]


def test_subtract_busy_outside_window_is_ignored():
    window = (_dt(2026, 4, 14, 9), _dt(2026, 4, 14, 17))
    busy = [
        (_dt(2026, 4, 13, 12), _dt(2026, 4, 13, 13)),  # day before
        (_dt(2026, 4, 14, 18), _dt(2026, 4, 14, 19)),  # after close
    ]
    assert subtract_busy_blocks(window, busy) == [window]


def test_subtract_multiple_disjoint_busy_blocks_produces_three_windows():
    window = (_dt(2026, 4, 14, 9), _dt(2026, 4, 14, 17))
    busy = [
        (_dt(2026, 4, 14, 10), _dt(2026, 4, 14, 11)),
        (_dt(2026, 4, 14, 14), _dt(2026, 4, 14, 15)),
    ]
    result = subtract_busy_blocks(window, busy)
    assert result == [
        (_dt(2026, 4, 14, 9), _dt(2026, 4, 14, 10)),
        (_dt(2026, 4, 14, 11), _dt(2026, 4, 14, 14)),
        (_dt(2026, 4, 14, 15), _dt(2026, 4, 14, 17)),
    ]


def test_subtract_inverted_window_returns_empty():
    """A window where start >= end should produce no free intervals."""
    window = (_dt(2026, 4, 14, 17), _dt(2026, 4, 14, 9))
    assert subtract_busy_blocks(window, []) == []


# ---------------------------------------------------------------------------
# generate_slots_in_window
# ---------------------------------------------------------------------------


def test_generate_slots_60min_every_30min_in_8_hour_window():
    window = (_dt(2026, 4, 14, 9), _dt(2026, 4, 14, 17))  # 9am-5pm = 8 hours
    slots = generate_slots_in_window(window, duration_min=60, interval_min=30)
    # First slot: 9:00-10:00. Last slot must end by 17:00 -> starts at 16:00.
    # 9:00, 9:30, 10:00, ..., 16:00 = 15 slots
    assert len(slots) == 15
    assert slots[0] == (_dt(2026, 4, 14, 9), _dt(2026, 4, 14, 10))
    assert slots[-1] == (_dt(2026, 4, 14, 16), _dt(2026, 4, 14, 17))


def test_generate_slots_exact_fit_at_end():
    window = (_dt(2026, 4, 14, 9), _dt(2026, 4, 14, 10))
    slots = generate_slots_in_window(window, duration_min=60, interval_min=30)
    assert slots == [(_dt(2026, 4, 14, 9), _dt(2026, 4, 14, 10))]


def test_generate_slots_duration_exceeds_window_returns_empty():
    window = (_dt(2026, 4, 14, 9), _dt(2026, 4, 14, 9, 30))  # 30-min window
    slots = generate_slots_in_window(window, duration_min=60, interval_min=30)
    assert slots == []


def test_generate_slots_zero_duration_returns_empty():
    window = (_dt(2026, 4, 14, 9), _dt(2026, 4, 14, 17))
    assert generate_slots_in_window(window, duration_min=0, interval_min=30) == []


def test_generate_slots_30min_every_15min():
    window = (_dt(2026, 4, 14, 9), _dt(2026, 4, 14, 10))
    slots = generate_slots_in_window(window, duration_min=30, interval_min=15)
    # 9:00, 9:15, 9:30 (last one ends at 10:00 — fits)
    assert len(slots) == 3
    assert slots[0] == (_dt(2026, 4, 14, 9), _dt(2026, 4, 14, 9, 30))
    assert slots[-1] == (_dt(2026, 4, 14, 9, 30), _dt(2026, 4, 14, 10))


# ---------------------------------------------------------------------------
# filter_by_time_preference
# ---------------------------------------------------------------------------


def test_filter_morning():
    slots = [
        (_dt(2026, 4, 14, 9), _dt(2026, 4, 14, 10)),
        (_dt(2026, 4, 14, 11), _dt(2026, 4, 14, 12)),
        (_dt(2026, 4, 14, 13), _dt(2026, 4, 14, 14)),
        (_dt(2026, 4, 14, 15), _dt(2026, 4, 14, 16)),
    ]
    result = filter_by_time_preference(slots, "morning")
    assert len(result) == 2
    assert all(s[0].hour < 12 for s in result)


def test_filter_afternoon():
    slots = [
        (_dt(2026, 4, 14, 9), _dt(2026, 4, 14, 10)),
        (_dt(2026, 4, 14, 12), _dt(2026, 4, 14, 13)),
        (_dt(2026, 4, 14, 15), _dt(2026, 4, 14, 16)),
        (_dt(2026, 4, 14, 17), _dt(2026, 4, 14, 18)),
    ]
    result = filter_by_time_preference(slots, "afternoon")
    assert len(result) == 2
    assert all(12 <= s[0].hour < 17 for s in result)


def test_filter_evening():
    slots = [
        (_dt(2026, 4, 14, 9), _dt(2026, 4, 14, 10)),
        (_dt(2026, 4, 14, 17), _dt(2026, 4, 14, 18)),
        (_dt(2026, 4, 14, 19), _dt(2026, 4, 14, 20)),
    ]
    result = filter_by_time_preference(slots, "evening")
    assert len(result) == 2
    assert all(s[0].hour >= 17 for s in result)


def test_filter_none_returns_input_unchanged():
    slots = [(_dt(2026, 4, 14, 9), _dt(2026, 4, 14, 10))]
    assert filter_by_time_preference(slots, None) == slots
    assert filter_by_time_preference(slots, "") == slots


def test_filter_unknown_preference_returns_input_unchanged():
    slots = [(_dt(2026, 4, 14, 9), _dt(2026, 4, 14, 10))]
    assert filter_by_time_preference(slots, "midnight") == slots


# ---------------------------------------------------------------------------
# compute_business_window
# ---------------------------------------------------------------------------

STD_HOURS = {
    "mon": ["09:00", "17:00"],
    "tue": ["09:00", "17:00"],
    "wed": ["09:00", "17:00"],
    "thu": ["09:00", "17:00"],
    "fri": ["09:00", "17:00"],
    "sat": None,
    "sun": None,
}


def test_compute_business_window_open_day():
    # April 14, 2026 is a Tuesday
    target = datetime(2026, 4, 14, 12, 0)
    window = compute_business_window(target, STD_HOURS, timezone="America/Toronto")
    assert window is not None
    assert window[0].hour == 9 and window[0].minute == 0
    assert window[1].hour == 17 and window[1].minute == 0


def test_compute_business_window_closed_day():
    # April 12, 2026 is a Sunday
    target = datetime(2026, 4, 12, 12, 0)
    assert compute_business_window(target, STD_HOURS) is None


def test_compute_business_window_malformed_hours_returns_none():
    bad = {"mon": ["nine"], "tue": "all day", "wed": ["09:00", "17:00", "extra"]}
    assert compute_business_window(datetime(2026, 4, 13, 12, 0), bad) is None  # mon
    assert compute_business_window(datetime(2026, 4, 14, 12, 0), bad) is None  # tue
    assert compute_business_window(datetime(2026, 4, 15, 12, 0), bad) is None  # wed


def test_compute_business_window_open_after_close_returns_none():
    """Hours where open >= close should be treated as misconfigured."""
    bad = {"tue": ["17:00", "09:00"]}
    assert compute_business_window(datetime(2026, 4, 14, 12, 0), bad) is None


# ---------------------------------------------------------------------------
# find_free_slots_pure — the integrated logic
# ---------------------------------------------------------------------------


def test_find_free_slots_pure_no_busy_full_day():
    window = (_dt(2026, 4, 14, 9), _dt(2026, 4, 14, 17))
    slots = find_free_slots_pure(
        business_window=window,
        busy_blocks=[],
        duration_min=60,
        max_slots=20,
    )
    assert len(slots) == 15  # 9:00 to 16:00 every 30 min
    assert slots[0][0].hour == 9


def test_find_free_slots_pure_caps_at_max_slots():
    window = (_dt(2026, 4, 14, 9), _dt(2026, 4, 14, 17))
    slots = find_free_slots_pure(
        business_window=window,
        busy_blocks=[],
        duration_min=60,
        max_slots=5,
    )
    assert len(slots) == 5


def test_find_free_slots_pure_subtracts_busy_block():
    window = (_dt(2026, 4, 14, 9), _dt(2026, 4, 14, 17))
    busy = [(_dt(2026, 4, 14, 12), _dt(2026, 4, 14, 13))]
    slots = find_free_slots_pure(
        business_window=window,
        busy_blocks=busy,
        duration_min=60,
        max_slots=20,
    )
    # Should NOT include any slot whose duration overlaps 12-13
    for s in slots:
        # Slot does not overlap [12, 13)
        overlap = s[0] < _dt(2026, 4, 14, 13) and s[1] > _dt(2026, 4, 14, 12)
        assert not overlap, f"Slot {s} overlaps the 12-13 busy block"


def test_find_free_slots_pure_buffer_pads_around_busy():
    """A 15-min buffer around a 12-13 busy block should block 11:00-12:00 too.

    Logic: with buffer 15, the busy block is padded to 11:45-13:15. A 60-min
    slot starting at 11:00 ends at 12:00, which now intersects the padded
    busy block (12:00 > 11:45). So 11:00 must NOT appear.
    """
    window = (_dt(2026, 4, 14, 9), _dt(2026, 4, 14, 17))
    busy = [(_dt(2026, 4, 14, 12), _dt(2026, 4, 14, 13))]
    slots = find_free_slots_pure(
        business_window=window,
        busy_blocks=busy,
        duration_min=60,
        buffer_min=15,
        max_slots=20,
    )
    starts = [s[0] for s in slots]
    assert _dt(2026, 4, 14, 11) not in starts, "11:00 slot should be blocked by 15-min buffer"
    assert _dt(2026, 4, 14, 13) not in starts, "13:00 slot should be blocked by 15-min buffer"
    # 10:30 is fine — ends at 11:30, well before padded busy starts at 11:45
    assert _dt(2026, 4, 14, 10, 30) in starts


def test_find_free_slots_pure_morning_filter():
    window = (_dt(2026, 4, 14, 9), _dt(2026, 4, 14, 17))
    slots = find_free_slots_pure(
        business_window=window,
        busy_blocks=[],
        duration_min=60,
        time_preference="morning",
        max_slots=20,
    )
    assert all(s[0].hour < 12 for s in slots)
    # 9:00, 9:30, 10:00, 10:30, 11:00, 11:30 = 6 slots in morning
    assert len(slots) == 6


def test_find_free_slots_pure_afternoon_filter_with_busy():
    window = (_dt(2026, 4, 14, 9), _dt(2026, 4, 14, 17))
    busy = [(_dt(2026, 4, 14, 14), _dt(2026, 4, 14, 15))]  # 2-3pm meeting
    slots = find_free_slots_pure(
        business_window=window,
        busy_blocks=busy,
        duration_min=60,
        time_preference="afternoon",
        max_slots=20,
    )
    # Afternoon = 12:00-17:00. A 2-3pm meeting + 60min slot rules:
    # Free windows: 9-14 and 15-17. Inside afternoon (>= 12, < 17):
    #   Window 1 contributes: 12:00, 12:30, 13:00 (ends at 14:00 — exact fit)
    #   Window 2 contributes: 15:00, 15:30, 16:00 (ends at 17:00 — exact fit)
    # Total = 6 slots
    assert len(slots) == 6, f"expected 6 afternoon slots, got {len(slots)}: {[s[0].strftime('%H:%M') for s in slots]}"
    starts = [s[0].strftime("%H:%M") for s in slots]
    assert "12:00" in starts
    assert "12:30" in starts
    assert "13:00" in starts
    assert "15:00" in starts
    assert "15:30" in starts
    assert "16:00" in starts
    assert "14:00" not in starts  # blocked by busy
    assert "13:30" not in starts  # would end at 14:30 — overlaps


def test_find_free_slots_pure_fully_busy_day_returns_empty():
    window = (_dt(2026, 4, 14, 9), _dt(2026, 4, 14, 17))
    busy = [(_dt(2026, 4, 14, 8), _dt(2026, 4, 14, 18))]
    slots = find_free_slots_pure(
        business_window=window,
        busy_blocks=busy,
        duration_min=60,
        max_slots=20,
    )
    assert slots == []


def test_find_free_slots_pure_multiple_calendars_merged():
    """Busy blocks from different calendars overlapping each other should
    merge — the user is busy 11-14 once, not twice."""
    window = (_dt(2026, 4, 14, 9), _dt(2026, 4, 14, 17))
    busy = [
        (_dt(2026, 4, 14, 11), _dt(2026, 4, 14, 13)),  # primary cal
        (_dt(2026, 4, 14, 12), _dt(2026, 4, 14, 14)),  # work cal
    ]
    slots = find_free_slots_pure(
        business_window=window,
        busy_blocks=busy,
        duration_min=60,
        max_slots=20,
    )
    # Free windows after merging busy: 9-11 and 14-17
    # 9:00, 9:30, 10:00 (ends 11:00 — fits) = 3 slots in morning
    # 14:00, 14:30, 15:00, 15:30, 16:00 = 5 slots in afternoon
    assert len(slots) == 8


# ---------------------------------------------------------------------------
# Test runner — works with or without pytest
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import inspect

    tests = [
        (name, fn)
        for name, fn in sorted(globals().items())
        if name.startswith("test_") and inspect.isfunction(fn)
    ]
    failures = []
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failures.append((name, str(e)))
            print(f"  FAIL  {name}: {e}")
        except Exception as e:
            failures.append((name, f"{type(e).__name__}: {e}"))
            print(f"  ERROR {name}: {type(e).__name__}: {e}")

    print()
    print(f"{len(tests) - len(failures)}/{len(tests)} tests passed")
    if failures:
        sys.exit(1)
