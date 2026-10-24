"""Typed settings loaded from environment variables, one prefix per folder (ORDERS_, VISION_, VERDICT_, ...).

Owner: platform (agents)
Step:  shared
Reads: environment variables (.env locally; see .env.example)
Emits: load_settings (a folder's settings dataclass from <PREFIX><FIELD> variables), require_secret, log_settings
Rules: secrets come only from the environment and are never logged; a missing secret fails with a clear message,
       never a hardcoded fallback; safety constants (thresholds, timeouts, transfer efficiencies) live in one named
       settings dataclass, are logged at startup, and are recorded in each verdict's versions

A folder declares its settings as a plain dataclass with defaults, for example:

    @dataclass(frozen=True)
    class VerdictSettings:
        stale_track_ms: int = 1500

    settings = load_settings(VerdictSettings, prefix="VERDICT_")   # VERDICT_STALE_TRACK_MS=2000 overrides it
    log_settings(settings)
"""

import dataclasses
import json
import logging
import os
import typing

logger = logging.getLogger("kitchen.settings")

SettingsT = typing.TypeVar("SettingsT")

# Words that mark a field as a secret. Secrets belong in require_secret(), not in a settings dataclass; this is the
# backstop that keeps one out of the startup log if it lands there anyway.
SECRET_WORDS = ("key", "secret", "token", "password")

TRUE_WORDS = ("1", "true", "yes", "on")
FALSE_WORDS = ("0", "false", "no", "off")


class MissingSettingError(RuntimeError):
    """A required setting or secret isn't in the environment. The message names the variable, never a value."""


def parse_value(raw: str, field_type: type, variable: str) -> bool | int | float | str:
    """Convert one environment variable's text to the field's type (bool, int, float or str)."""
    if field_type is bool:
        word = raw.strip().lower()
        if word in TRUE_WORDS:
            return True
        if word in FALSE_WORDS:
            return False
        raise ValueError(f"{variable} must be one of {TRUE_WORDS + FALSE_WORDS}, got {raw!r}")
    if field_type in (int, float, str):
        try:
            return field_type(raw)
        except ValueError:
            raise ValueError(f"{variable} must be a {field_type.__name__}, got {raw!r}") from None
    raise TypeError(f"{variable}: settings fields must be bool, int, float or str, not {field_type}")


def load_settings(settings_class: type[SettingsT], prefix: str) -> SettingsT:
    """Build `settings_class` (a dataclass) from <PREFIX><FIELD_NAME> environment variables.

    A field with no variable keeps its dataclass default; a field with no default and no variable raises
    MissingSettingError naming the variable. Values are converted by the field's annotated type.
    """
    field_types = typing.get_type_hints(settings_class)
    values = {}
    for field in dataclasses.fields(settings_class):
        variable = prefix + field.name.upper()
        raw = os.environ.get(variable)
        has_default = field.default is not dataclasses.MISSING or field.default_factory is not dataclasses.MISSING
        if raw is None and not has_default:
            raise MissingSettingError(f"{variable} is not set; add it to .env (see .env.example)")
        if raw is not None:
            values[field.name] = parse_value(raw, field_types[field.name], variable)
    return settings_class(**values)


def require_secret(name: str) -> str:
    """Return the secret in environment variable `name`, or raise MissingSettingError naming it.

    There is never a fallback value: a missing key stops the caller with a clear message (AGENTS.md, Data and
    secrets). The value itself never appears in an error or a log.
    """
    value = os.environ.get(name, "")
    if not value:
        raise MissingSettingError(f"secret {name} is not set; add it to .env (see .env.example)")
    return value


def is_secret_name(name: str) -> bool:
    """True when a field name looks like it holds a secret (an API key, a token, a password)."""
    lowered = name.lower()
    return any(word in lowered for word in SECRET_WORDS)


def log_settings(settings: object) -> dict:
    """Log a settings dataclass as one JSON line at startup and return what was logged.

    Every safety constant is visible in the log of the run that used it. Fields that look like secrets are left out.
    """
    values = {
        field.name: getattr(settings, field.name)
        for field in dataclasses.fields(settings)
        if not is_secret_name(field.name)
    }
    record = {"event": "settings", "settings": type(settings).__name__, "values": values}
    logger.info(json.dumps(record, sort_keys=True))
    return record
