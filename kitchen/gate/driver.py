"""GateDriver implementations: FakeGate (prints and records calls) now, the GPIO driver for servo, lights and buzzer later.

Owner: outputs (agents)
Step:  gate and screens
Reads: gate state from service.py (idle | pass | hold | fault, ticket number)
Emits: servo position, red/green light, buzzer
Rules: fail closed: any driver exception means fault

Built so far: FakeGate. The GPIO driver waits for the hardware (M2).
"""

GATE_STATES = ("idle", "pass", "hold", "fault")


class FakeGate:
    """GateDriver that prints each state and records every show() call, for tests, the end-to-end harness and demos.

    Set `error` to make show() raise it (except for "fault"), so callers can test that a driver failure ends in fault.
    """

    def __init__(self, quiet: bool = False):
        self.calls: list[tuple[str, int | None]] = []
        self.quiet = quiet
        self.error: Exception | None = None

    def show(self, state: str, ticket_number: int | None = None) -> None:
        """Record and print the state. Rejects anything that isn't a gate state, as real hardware would."""
        if state not in GATE_STATES:
            raise ValueError(f"unknown gate state {state!r}")
        if self.error is not None and state != "fault":
            raise self.error
        self.calls.append((state, ticket_number))
        if not self.quiet:
            print(f"[gate] {state}" + (f" #{ticket_number}" if ticket_number is not None else ""))

    @property
    def state(self) -> str | None:
        """The last state shown, or None before the first show()."""
        return self.calls[-1][0] if self.calls else None
