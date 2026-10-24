"""FakeClassifier: stands in for the partner's interaction classifiers (zone rules, ConvLSTM) with scripted labels.

Owner: platform (agents)
Step:  shared
Reads: PairWindows (only their a_id and b_id, to look up the script)
Emits: scripted (action, probability) lists, as InteractionClassifier promises
Rules: blind classifier: it never looks at features, clip or tensor; set_unavailable() makes classify() raise, so the
       interactions wrapper's fallback to zone rules can be tested (rules stay as the fallback)
"""

from kitchen.shared.interfaces import PairWindow
from kitchen.shared.models import Action

# What an unscripted pair gets: most candidate pairs in a kitchen frame are things passing near each other.
UNSCRIPTED_LABELS: list[tuple[Action, float]] = [("unimportant", 1.0)]


class FakeClassifier:
    """An InteractionClassifier that answers from a script keyed by (a_id, b_id)."""

    def __init__(
        self,
        labels_by_pair: dict[tuple[str, str], list[tuple[Action, float]]] | None = None,
        *,
        name: str = "zone_rules",
        version: str = "fake",
    ) -> None:
        self.name = name
        self.version = version
        self.labels_by_pair = dict(labels_by_pair or {})
        self._unavailable = False

    def set_unavailable(self, unavailable: bool = True) -> None:
        """Make classify() raise RuntimeError until called again with False.

        contracts/apis.md names no classifier exception, and the interactions wrapper falls back to zone rules on any
        error, so a plain RuntimeError is enough to test that path.
        """
        self._unavailable = unavailable

    def classify(self, window: PairWindow) -> list[tuple[Action, float]]:
        """Return the scripted labels for the window's pair, or `unimportant` for a pair with no script."""
        if self._unavailable:
            raise RuntimeError("classifier unavailable (FakeClassifier.set_unavailable)")
        return list(self.labels_by_pair.get((window.a_id, window.b_id), UNSCRIPTED_LABELS))
