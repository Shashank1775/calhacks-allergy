"""Tests for kitchen/shared/clock.py: FakeClock is fully driven by hand, and neither clock runs backwards.

Owner: platform (agents)
Step:  shared
Reads: kitchen.shared.clock
Emits: nothing
Rules: time runs forward
"""

import pytest

from kitchen.shared.clock import FakeClock, SystemClock


def test_fake_clock_starts_where_it_is_told():
    assert FakeClock().now_ms() == 0
    assert FakeClock(start_ms=120_000).now_ms() == 120_000


def test_fake_clock_moves_only_when_advanced_or_set():
    clock = FakeClock()
    assert clock.now_ms() == clock.now_ms() == 0

    clock.advance(33)
    clock.advance(33)
    assert clock.now_ms() == 66

    clock.set(1000)
    assert clock.now_ms() == 1000


@pytest.mark.safety
def test_fake_clock_refuses_to_go_backwards():
    """Time runs forward: a replay that steps back in time would reorder edges."""
    clock = FakeClock(start_ms=1000)
    with pytest.raises(ValueError, match="time runs forward"):
        clock.advance(-1)
    with pytest.raises(ValueError, match="time runs forward"):
        clock.set(999)
    assert clock.now_ms() == 1000


def test_fake_clock_refuses_a_start_before_shift_start():
    with pytest.raises(ValueError):
        FakeClock(start_ms=-5)


def test_system_clock_starts_near_zero_and_never_goes_back():
    clock = SystemClock()
    readings = [clock.now_ms() for _ in range(1000)]

    assert 0 <= readings[0] < 1000
    assert readings == sorted(readings)
