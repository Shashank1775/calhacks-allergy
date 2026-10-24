"""Tests for kitchen/shared/config.py: settings from prefixed env vars, secrets that fail clearly, one log line.

Owner: platform (agents)
Step:  shared
Reads: kitchen.shared.config, environment variables set with monkeypatch
Emits: nothing
Rules: a missing secret names the variable and never shows a value; secrets never reach the settings log
"""

import json
import logging
from dataclasses import dataclass

import pytest

from kitchen.shared.config import MissingSettingError, load_settings, log_settings, require_secret


@dataclass(frozen=True)
class ExampleSettings:
    pass_margin: float = 0.01
    stale_track_ms: int = 1500
    use_lstm: bool = False
    camera_id: str = "cam0"


@dataclass(frozen=True)
class NeedsPort:
    port: int


def test_defaults_apply_when_no_variable_is_set(monkeypatch):
    for name in ("PASS_MARGIN", "STALE_TRACK_MS", "USE_LSTM", "CAMERA_ID"):
        monkeypatch.delenv(f"EXAMPLE_{name}", raising=False)

    assert load_settings(ExampleSettings, prefix="EXAMPLE_") == ExampleSettings()


def test_variables_override_defaults_and_convert_by_type(monkeypatch):
    monkeypatch.setenv("EXAMPLE_PASS_MARGIN", "0.05")
    monkeypatch.setenv("EXAMPLE_STALE_TRACK_MS", "2000")
    monkeypatch.setenv("EXAMPLE_USE_LSTM", "true")
    monkeypatch.setenv("EXAMPLE_CAMERA_ID", "cam1")

    settings = load_settings(ExampleSettings, prefix="EXAMPLE_")
    assert settings == ExampleSettings(pass_margin=0.05, stale_track_ms=2000, use_lstm=True, camera_id="cam1")


def test_another_folders_prefix_is_ignored(monkeypatch):
    monkeypatch.setenv("VISION_STALE_TRACK_MS", "9")
    monkeypatch.delenv("EXAMPLE_STALE_TRACK_MS", raising=False)

    assert load_settings(ExampleSettings, prefix="EXAMPLE_").stale_track_ms == 1500


@pytest.mark.parametrize("raw", ["yes", "1", "ON"])
def test_bool_accepts_common_true_words(monkeypatch, raw):
    monkeypatch.setenv("EXAMPLE_USE_LSTM", raw)
    assert load_settings(ExampleSettings, prefix="EXAMPLE_").use_lstm is True


@pytest.mark.parametrize(("variable", "raw"), [("EXAMPLE_STALE_TRACK_MS", "soon"), ("EXAMPLE_USE_LSTM", "maybe")])
def test_a_bad_value_names_its_variable(monkeypatch, variable, raw):
    monkeypatch.setenv(variable, raw)
    with pytest.raises(ValueError, match=variable):
        load_settings(ExampleSettings, prefix="EXAMPLE_")


def test_a_required_setting_without_a_variable_names_it(monkeypatch):
    monkeypatch.delenv("ORDERS_PORT", raising=False)
    with pytest.raises(MissingSettingError, match="ORDERS_PORT"):
        load_settings(NeedsPort, prefix="ORDERS_")


def test_require_secret_returns_the_value(monkeypatch):
    monkeypatch.setenv("CARTESIA_API_KEY", "sk-test-only")
    assert require_secret("CARTESIA_API_KEY") == "sk-test-only"


@pytest.mark.parametrize("value", [None, ""])
def test_a_missing_or_empty_secret_names_the_variable(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("CARTESIA_API_KEY", raising=False)
    else:
        monkeypatch.setenv("CARTESIA_API_KEY", value)
    with pytest.raises(MissingSettingError, match="CARTESIA_API_KEY"):
        require_secret("CARTESIA_API_KEY")


def test_log_settings_writes_one_json_line_with_every_value(caplog):
    with caplog.at_level(logging.INFO, logger="kitchen.settings"):
        record = log_settings(ExampleSettings())

    assert len(caplog.records) == 1
    logged = json.loads(caplog.records[0].getMessage())
    assert logged == record
    assert logged["settings"] == "ExampleSettings"
    assert logged["values"] == {"pass_margin": 0.01, "stale_track_ms": 1500, "use_lstm": False, "camera_id": "cam0"}


@dataclass(frozen=True)
class LeakySettings:
    cartesia_api_key: str = "sk-test-only"
    stale_track_ms: int = 1500


def test_log_settings_leaves_out_fields_that_look_like_secrets(caplog):
    with caplog.at_level(logging.INFO, logger="kitchen.settings"):
        log_settings(LeakySettings())

    assert "sk-test-only" not in caplog.text
    assert json.loads(caplog.records[0].getMessage())["values"] == {"stale_track_ms": 1500}
