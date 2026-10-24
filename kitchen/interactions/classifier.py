"""Classifier wrapper: gives each PairWindow to the adopted ConvLSTM when healthy, else to zone rules.

Owner: partner (written by an agent at the owner's request)
Step:  Interactions (candidate pairs, features, zone rules)
Reads: PairWindow; InteractionClassifier implementations: zone_rules.py, and a primary (the partner's kitchen/lstm,
       only after the benchmark ADR adopts it; none until then)
Emits: (labels, classifier): a list of (action, probability) plus {"name", "version"} of the classifier that produced
       it, as interaction_event.classifier records it; fallback_count for health checks
Rules: 7 zone rules are the fallback whenever the primary is missing, raises, returns something invalid, or takes
       longer than primary_timeout_ms; 1 a wash is judged by zone rules alone, because only a self pair can be a wash
       and the ConvLSTM judges contact and movement between two boxes

The primary runs in a daemon thread so a hung model can be abandoned: the window goes to zone rules, and later windows
skip the primary (straight to zone rules, without waiting) until the hung call returns.
"""

import logging
import math
import numbers
import threading

from kitchen.interactions.zone_rules import ZoneRules
from kitchen.shared.interfaces import InteractionClassifier, PairWindow
from kitchen.shared.models import Action

logger = logging.getLogger("kitchen.interactions.classifier")

# interaction_event.classifier.name allows only these.
CLASSIFIER_NAMES = ("zone_rules", "lstm", "sim_ground_truth", "manual")

# What a primary may answer. Not clean.wash: a wash lowers risk, and only zone rules may judge one, on a self pair.
PRIMARY_ACTIONS = ("transfer.contact", "transfer.scoop", "transfer.wipe", "unimportant")

Labels = list[tuple[Action, float]]


def checked_labels(labels: object) -> Labels:
    """Return a primary's answer as clean (action, float) pairs, or raise ValueError if any part of it is off.

    An empty answer is refused too: the primary must say "unimportant" to mean nothing happened, so a model that
    silently returns nothing can't hide a transfer.
    """
    if not isinstance(labels, list) or not labels:
        raise ValueError(f"expected a non-empty list of (action, probability), got {labels!r}")
    checked = []
    for item in labels:
        if not isinstance(item, tuple | list) or len(item) != 2:
            raise ValueError(f"expected (action, probability), got {item!r}")
        action, probability = item
        if action not in PRIMARY_ACTIONS:
            raise ValueError(f"action {action!r} is not one a primary may give")
        is_number = isinstance(probability, numbers.Real) and not isinstance(probability, bool)
        if not is_number or not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
            raise ValueError(f"probability {probability!r} for {action} is not in [0, 1]")
        checked.append((action, float(probability)))
    return checked


def call_in_thread(classifier: InteractionClassifier, window: PairWindow, timeout_s: float) -> tuple:
    """Run classifier.classify(window) in a daemon thread for at most timeout_s seconds.

    Returns (thread, result). The thread is still alive if the call timed out; result holds "labels" or "error".
    The timeout is real elapsed time on purpose: it bounds compute time, not shift time, so it doesn't use a Clock.
    """
    result: dict = {}

    def run() -> None:
        try:
            result["labels"] = classifier.classify(window)
        except BaseException as error:  # even SystemExit from inside the model means zone rules judge this window
            result["error"] = error

    thread = threading.Thread(target=run, name="interactions-primary", daemon=True)
    thread.start()
    thread.join(timeout_s)
    return thread, result


class FallbackClassifier:
    """Zone rules, optionally behind a primary classifier. Every answer says which classifier gave it."""

    def __init__(
        self, rules: ZoneRules, primary: InteractionClassifier | None = None, *, timeout_ms: int | None = None
    ) -> None:
        if primary is not None and primary.name not in CLASSIFIER_NAMES:
            # Caught at startup, not at the first event, where the event schema would refuse the name mid-shift.
            raise ValueError(f"primary classifier name {primary.name!r} is not one of {CLASSIFIER_NAMES}")
        self.rules = rules
        self.primary = primary
        self.timeout_ms = rules.settings.primary_timeout_ms if timeout_ms is None else timeout_ms
        self.fallback_count = 0
        self._hung: threading.Thread | None = None
        self._failing = False

    def classify_window(self, window: PairWindow) -> tuple[Labels, dict]:
        """Label one window and say who labelled it: the primary when it answers well and in time, else zone rules."""
        if self.primary is None or window.a_id == window.b_id:
            return self.from_rules(window)
        if window.clip is None or window.tensor is None:
            return self.fall_back(window, "the window has no clip")
        if self._hung is not None and self._hung.is_alive():
            return self.fall_back(window, "the primary is still stuck on an earlier window")

        thread, result = call_in_thread(self.primary, window, self.timeout_ms / 1000)
        if thread.is_alive():
            self._hung = thread
            return self.fall_back(window, f"the primary took longer than {self.timeout_ms} ms")
        if "labels" not in result:
            # It raised (any BaseException, even SystemExit), or its thread ended with no answer: zone rules judge.
            return self.fall_back(window, f"the primary gave no answer: {result.get('error')!r}")
        try:
            labels = checked_labels(result["labels"])
        except ValueError as error:
            return self.fall_back(window, f"the primary answered badly: {error}")

        if self._failing:
            logger.info("primary %s answering again after %s fallbacks", self.primary.name, self.fallback_count)
            self._failing = False
        return labels, {"name": self.primary.name, "version": self.primary.version}

    def from_rules(self, window: PairWindow) -> tuple[Labels, dict]:
        """Zone rules' labels for the window, named as zone rules."""
        return self.rules.classify(window), {"name": self.rules.name, "version": self.rules.version}

    def fall_back(self, window: PairWindow, reason: str) -> tuple[Labels, dict]:
        """Count a fallback, log the first of a run of them, and answer with zone rules (safety rule 7)."""
        self.fallback_count += 1
        if not self._failing:
            logger.error("falling back to zone rules for %s/%s: %s", window.a_id, window.b_id, reason)
            self._failing = True
        return self.from_rules(window)
