"""The classifier wrapper: a healthy primary answers, anything else lands on zone rules, and the answer says who gave it.

Owner: partner (written by an agent at the owner's request)
Step:  Interactions (tests)
Reads: kitchen.interactions.classifier, zone_rules, events; FakeClassifier
Emits: pytest tests
Rules: 7 zone rules whenever the primary is missing, raises, answers badly or hangs; 1 only zone rules judge washes
"""

import threading

import numpy as np
import pytest

from kitchen.interactions.classifier import FallbackClassifier
from kitchen.interactions.events import InteractionStep
from kitchen.interactions.settings import InteractionSettings
from kitchen.interactions.tests.scenes import KNIFE, SCENES, ZONES
from kitchen.interactions.zone_rules import ZoneRules
from kitchen.shared.contract_tests import contract_fixture
from kitchen.shared.fakes import FakeClassifier
from kitchen.shared.interfaces import PairWindow

RULES = ZoneRules(InteractionSettings())
ZONE_RULES = {"name": "zone_rules", "version": RULES.version}
SCRIPTED = [("transfer.contact", 0.7)]


def knife_in_bin_window(*, with_clip: bool = True, self_pair: bool = False) -> PairWindow:
    """Half a second of a knife resting in its bin, with the contract fixture's clip and an empty tensor."""
    base = contract_fixture("interaction_features", "knife-in-bin")
    if self_pair:
        base = {**base, "b_id": base["a_id"], "b_kind": "tool", "zone_kind": "wash"}
    rows = [{**base, "t_ms": base["t_ms"] + t} for t in range(0, 501, 33)]
    clip = contract_fixture("interaction_clip", "live-unlabeled") if with_clip else None
    tensor = np.zeros((12, 3, 128, 128), dtype=np.uint8) if with_clip else None
    return PairWindow(a_id=base["a_id"], b_id=base["b_id"], features=rows, clip=clip, tensor=tensor)


def primary(labels=None) -> FakeClassifier:
    """A healthy stand-in for the ConvLSTM that scripts the knife-in-bin pair."""
    window = knife_in_bin_window()
    return FakeClassifier({(window.a_id, window.b_id): labels or SCRIPTED}, name="lstm", version="fake-1")


class HangingClassifier(FakeClassifier):
    """A primary that blocks until released, like a model stuck on a device."""

    def __init__(self) -> None:
        super().__init__({}, name="lstm", version="hangs")
        self.release = threading.Event()

    def classify(self, window):
        self.release.wait(10)
        return super().classify(window)


class AnswerClassifier(FakeClassifier):
    """A primary that returns exactly what it is told to, valid or not."""

    def __init__(self, answer) -> None:
        super().__init__({}, name="lstm", version="answers")
        self.answer = answer
        self.seen: list[PairWindow] = []

    def classify(self, window):
        self.seen.append(window)
        return self.answer


def test_without_a_primary_zone_rules_answer():
    labels, classifier = FallbackClassifier(RULES).classify_window(knife_in_bin_window())

    assert classifier == ZONE_RULES
    assert labels == RULES.classify(knife_in_bin_window())


def test_a_healthy_primary_answers_with_its_own_name_and_version():
    wrapper = FallbackClassifier(RULES, primary())

    labels, classifier = wrapper.classify_window(knife_in_bin_window())

    assert labels == SCRIPTED
    assert classifier == {"name": "lstm", "version": "fake-1"}
    assert wrapper.fallback_count == 0


@pytest.mark.safety
def test_a_raising_primary_falls_back_to_zone_rules_and_is_counted():
    failing = primary()
    failing.set_unavailable()
    wrapper = FallbackClassifier(RULES, failing)

    labels, classifier = wrapper.classify_window(knife_in_bin_window())

    assert classifier == ZONE_RULES
    assert labels == RULES.classify(knife_in_bin_window())
    assert wrapper.fallback_count == 1


@pytest.mark.safety
@pytest.mark.parametrize(
    "answer",
    [
        [],
        None,
        "transfer.contact",
        [("transfer.teleport", 0.5)],
        [("transfer.contact", 1.5)],
        [("transfer.contact", -0.1)],
        [("transfer.contact", float("nan"))],
        [("transfer.contact", True)],
        [("transfer.contact",)],
        [("clean.wash", 0.99)],  # a wash only ever comes from zone rules
    ],
)
def test_a_bad_answer_falls_back_to_zone_rules(answer):
    wrapper = FallbackClassifier(RULES, AnswerClassifier(answer))

    _, classifier = wrapper.classify_window(knife_in_bin_window())

    assert classifier == ZONE_RULES
    assert wrapper.fallback_count == 1


def test_numpy_probabilities_are_accepted_as_floats():
    wrapper = FallbackClassifier(RULES, AnswerClassifier([("transfer.scoop", np.float32(0.25))]))

    labels, classifier = wrapper.classify_window(knife_in_bin_window())

    assert classifier["name"] == "lstm"
    assert labels == [("transfer.scoop", 0.25)] and isinstance(labels[0][1], float)


@pytest.mark.safety
def test_a_hanging_primary_falls_back_and_is_skipped_until_it_returns():
    hanging = HangingClassifier()
    wrapper = FallbackClassifier(RULES, hanging, timeout_ms=50)

    first = wrapper.classify_window(knife_in_bin_window())
    second = wrapper.classify_window(knife_in_bin_window())  # skips the stuck primary instead of waiting again

    assert first[1] == ZONE_RULES and second[1] == ZONE_RULES
    assert wrapper.fallback_count == 2

    hanging.release.set()
    wrapper._hung.join(5)
    _, classifier = wrapper.classify_window(knife_in_bin_window())
    assert classifier == {"name": "lstm", "version": "hangs"}


@pytest.mark.safety
def test_washes_and_clipless_windows_go_to_zone_rules():
    spy = AnswerClassifier(SCRIPTED)
    wrapper = FallbackClassifier(RULES, spy)

    _, wash_classifier = wrapper.classify_window(knife_in_bin_window(self_pair=True))
    _, clipless_classifier = wrapper.classify_window(knife_in_bin_window(with_clip=False))

    assert wash_classifier == ZONE_RULES
    assert clipless_classifier == ZONE_RULES
    assert spy.seen == []  # the primary never saw either
    assert wrapper.fallback_count == 1  # a wash isn't a fallback; a missing clip is


def test_pair_window_routing_zone_rules_read_features_the_primary_gets_the_clip():
    spy = AnswerClassifier(SCRIPTED)
    window = knife_in_bin_window()

    FallbackClassifier(RULES, spy).classify_window(window)

    assert spy.seen[0].clip is window.clip and spy.seen[0].tensor is window.tensor
    assert RULES.classify(window) == RULES.classify(knife_in_bin_window(with_clip=False))


def test_a_primary_with_a_name_events_cannot_carry_is_refused_at_startup():
    with pytest.raises(ValueError, match="convlstm"):
        FallbackClassifier(RULES, FakeClassifier(name="convlstm"))


@pytest.mark.safety
def test_events_name_zone_rules_when_the_primary_fails():
    failing = FakeClassifier(name="lstm", version="fake-1")
    failing.set_unavailable()
    step = InteractionStep(ZONES, classifier=FallbackClassifier(RULES, failing), with_clips=True)
    frames = SCENES["scoop_then_plate"].frames()
    masks = np.random.default_rng(3).random((len(frames), 128, 128)).astype(np.float32)

    events = [event for index, frame in enumerate(frames) for event in step.process(frame, masks[index])]
    events += step.finish()

    assert {event["action"] for event in events} == {"transfer.scoop", "transfer.contact"}
    assert all(event["classifier"] == ZONE_RULES for event in events)


def test_events_name_a_healthy_primary():
    scripted = FakeClassifier({(KNIFE, "plate-t1"): [("transfer.contact", 0.6)]}, name="lstm", version="fake-1")
    step = InteractionStep(ZONES, classifier=FallbackClassifier(RULES, scripted), with_clips=True)
    frames = SCENES["scoop_then_plate"].frames()
    masks = np.zeros((len(frames), 128, 128), dtype=np.float32)

    events = [event for index, frame in enumerate(frames) for event in step.process(frame, masks[index])]
    events += step.finish()

    from_primary = [event for event in events if event["classifier"]["name"] == "lstm"]
    assert from_primary and all(event["b_id"] == "plate-t1" for event in from_primary)
