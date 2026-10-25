"""Tests for kitchen/shared/clock.py: FakeClock is fully driven by hand, HeldClock waits for start(), and no clock
runs backwards.

Owner: platform (agents)
Step:  shared
Reads: kitchen.shared.clock
Emits: nothing
Rules: time runs forward
"""

import threading
import time

import pytest

from kitchen.shared.clock import FakeClock, HeldClock, SystemClock


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


def test_held_clock_reads_zero_until_started_then_counts_from_the_start():
    clock = HeldClock()
    time.sleep(0.05)  # the user boxing zones before pressing Start: not counted
    assert clock.now_ms() == 0

    clock.start()
    assert 0 <= clock.now_ms() < 50
    time.sleep(0.05)
    assert clock.now_ms() >= 50


@pytest.mark.safety
def test_held_clock_second_start_is_a_no_op_and_it_never_goes_back():
    """Time runs forward: a later start() (every pushed frame calls it) must not reset the clock to 0."""
    clock = HeldClock()
    clock.start()
    time.sleep(0.03)
    before = clock.now_ms()

    threads = [threading.Thread(target=clock.start) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    readings = [clock.now_ms() for _ in range(1000)]

    assert readings[0] >= before >= 30
    assert readings == sorted(readings)
