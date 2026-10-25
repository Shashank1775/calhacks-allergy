"""The only way code reads time: t_ms since shift start, injectable so replays and tests are deterministic.

Owner: platform (agents)
Step:  shared
Reads: the system monotonic clock (SystemClock and HeldClock)
Emits: SystemClock (live), HeldClock (live, held at 0 until the first frame), FakeClock (tests and replay; advanced
       by hand)
Rules: logic never calls time.time() or datetime.now(); it takes a Clock (AGENTS.md, Engineering conventions);
       time runs forward, so no clock ever goes backwards
"""

import threading
import time


class SystemClock:
    """The live clock: milliseconds since this clock was created, which is shift start for the process using it.

    It reads the monotonic clock, so a wall-clock change (NTP, daylight saving) can never move t_ms backwards.
    """

    def __init__(self) -> None:
        self._start_ns = time.monotonic_ns()

    def now_ms(self) -> int:
        """Return t_ms: whole milliseconds since this clock was created."""
        return (time.monotonic_ns() - self._start_ns) // 1_000_000


class HeldClock:
    """A live clock that reads 0 until start(), then counts milliseconds since start() like SystemClock.

    The local runner fed from the web app (--from-web) uses it: the user boxes the kitchen zones before pressing Start,
    and shift start (and the clean window after it) must begin at the first frame, not when the process started. A
    second start() is a no-op, so the clock never jumps back to 0 (time runs forward).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._start_ns: int | None = None

    def start(self) -> None:
        """Start counting from now. Safe to call from several threads; only the first call counts."""
        with self._lock:
            if self._start_ns is None:
                self._start_ns = time.monotonic_ns()

    def now_ms(self) -> int:
        """Return t_ms: 0 before start(), then whole milliseconds since start()."""
        start_ns = self._start_ns
        if start_ns is None:
            return 0
        return (time.monotonic_ns() - start_ns) // 1_000_000


class FakeClock:
    """A clock tests and replays move by hand, so every t_ms they see is chosen, not measured."""

    def __init__(self, start_ms: int = 0) -> None:
        if start_ms < 0:
            raise ValueError(f"t_ms starts at shift start (0), got {start_ms}")
        self._t_ms = start_ms

    def now_ms(self) -> int:
        """Return the t_ms the test last set."""
        return self._t_ms

    def advance(self, ms: int) -> None:
        """Move time forward by `ms`. A negative step is refused (safety rule: time runs forward)."""
        if ms < 0:
            raise ValueError(f"time runs forward: can't advance by {ms} ms")
        self._t_ms += ms

    def set(self, t_ms: int) -> None:
        """Jump to `t_ms`. Going backwards is refused (safety rule: time runs forward)."""
        if t_ms < self._t_ms:
            raise ValueError(f"time runs forward: can't go from {self._t_ms} back to {t_ms}")
        self._t_ms = t_ms
