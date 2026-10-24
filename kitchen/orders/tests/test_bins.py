"""Tests for the hard-coded demo bins."""

import pytest

from kitchen.orders.bins import DEMO_BINS, bin_profiles, save_bins


@pytest.mark.safety
def test_every_demo_bin_matches_the_contract(check_schema):
    for profile in bin_profiles().values():
        assert check_schema("allergen_profile", profile) == []


def test_save_bins_writes_every_bin(state):
    save_bins(state)
    assert sorted(state.bin_profiles) == sorted(DEMO_BINS)
    assert state.bin_profiles["bin-1"] == {
        "probabilities": {"peanut": 0.98, "soy": 0.9},
        "unknown": False,
        "source": "manual",
    }


def test_an_unsure_bin_can_be_marked_unknown(state):
    save_bins(state, {"bin-9": {"label": "house sauce", "allergens": {}, "unknown": True}})
    assert state.bin_profiles["bin-9"]["unknown"] is True


@pytest.mark.safety
@pytest.mark.parametrize(
    "bins",
    [
        {"bin-1": {"label": "nuts", "allergens": {"nuts": 0.9}}},  # not an allergen unit
        {"bin-1": {"label": "satay", "allergens": {"peanut": 1.5}}},  # not a probability
        {"slot-1": {"label": "satay", "allergens": {"peanut": 0.9}}},  # not a bin ID
    ],
)
def test_a_typo_in_the_bins_fails_loudly(bins, state):
    with pytest.raises(ValueError):
        save_bins(state, bins)
    assert state.bin_profiles == {}
