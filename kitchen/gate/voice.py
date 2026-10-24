"""Voice: turn a verdict into a short line with numbers as words ("Hold seven"), and say it.
Speaker implementations: FakeSpeaker now, Cartesia Sonic later. Non-blocking; stale lines are dropped.

Owner: outputs (agents)
Step:  gate and screens
Reads: Verdict, Ticket.number
Emits: audio out (Speaker)
Rules: voice is never on the critical path to HOLD: the gate shows its state before anything is said

Built so far: spoken_line() and FakeSpeaker. The Cartesia Sonic speaker comes with its spike.
"""

ONES = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen"
).split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def number_words(n: int) -> str:
    """Say a ticket number the way an expediter would: 7 → "seven", 42 → "forty two", 105 → "one oh five".

    Numbers of three digits or more are read digit by digit, with 0 as "oh", which is shorter and clearer on a pass.
    """
    if n < 0:
        raise ValueError(f"ticket numbers are not negative: {n}")
    if n < 20:
        return ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return TENS[tens] if ones == 0 else f"{TENS[tens]} {ONES[ones]}"
    return " ".join("oh" if digit == "0" else ONES[int(digit)] for digit in str(n))


def allergen_words(allergen_unit_ids: list[str]) -> str:
    """Join allergen IDs into speech: ["peanut"] → "peanut", ["milk", "tree_nut"] → "milk and tree nut"."""
    words = [unit.replace("_", " ") for unit in allergen_unit_ids]
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


def spoken_line(verdict: dict, ticket_number: int) -> str | None:
    """The line to say for a verdict: "Hold seven, peanut" for a hold (allergens only if flagged), None for a pass.

    Passes aren't spoken: the green light says it, and a quiet pass keeps the holds noticeable.
    """
    if verdict["decision"] == "pass":
        return None
    line = f"Hold {number_words(ticket_number)}"
    flagged = [item["allergen_unit_id"] for item in verdict["flagged"]]
    return f"{line}, {allergen_words(flagged)}" if flagged else line


class FakeSpeaker:
    """Speaker that prints each line and records it. It speaks instantly, so it never has a stale line to drop."""

    def __init__(self, quiet: bool = False):
        self.lines: list[str] = []
        self.quiet = quiet

    def say(self, text: str) -> None:
        """Record and print the line. Never blocks."""
        self.lines.append(text)
        if not self.quiet:
            print(f"[voice] {text}")
