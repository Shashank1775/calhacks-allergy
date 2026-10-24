"""Contract suite for InteractionClassifier: what contracts/apis.md promises, and nothing else.

Owner: platform (agents)
Step:  shared
Reads: the make_classifier fixture; contracts/fixtures/valid (interaction_features, interaction_clip)
Emits: pytest tests marked `contract`
Rules: blind classifier: the window it gets carries vector features, box masks and a change mask only; its answer is
       a list of contract actions with probabilities

classify() tests need numpy for the window's tensor and skip without it (numpy comes with the clip builder).
"""

import typing

import pytest

from kitchen.shared.contract_tests import contract_fixture
from kitchen.shared.interfaces import PairWindow
from kitchen.shared.models import Action


def _pair_window() -> PairWindow:
    """A knife-at-a-bin window from the contract fixtures, with an all-zero [T, C, H, W] tensor."""
    np = pytest.importorskip("numpy")
    clip = contract_fixture("interaction_clip", "live-unlabeled")
    shape = clip["shape"]
    tensor = np.zeros((shape["T"], shape["C"], shape["H"], shape["W"]), dtype=np.uint8)
    features = [contract_fixture("interaction_features", "knife-in-bin")]
    return PairWindow(a_id=clip["a_id"], b_id=clip["b_id"], features=features, clip=clip, tensor=tensor)


@pytest.mark.contract
def test_name_is_zone_rules_or_lstm(make_classifier):
    assert make_classifier().name in ("zone_rules", "lstm")


@pytest.mark.contract
def test_classifier_version_is_a_nonempty_string(make_classifier):
    version = make_classifier().version
    assert isinstance(version, str) and version


@pytest.mark.contract
def test_classify_returns_contract_actions_with_probabilities(make_classifier):
    labels = make_classifier().classify(_pair_window())

    assert isinstance(labels, list)
    for action, probability in labels:
        assert action in typing.get_args(Action)
        assert 0.0 <= probability <= 1.0
