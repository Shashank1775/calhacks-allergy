"""Typed settings loaded from environment variables, one prefix per folder (ORDERS_, VISION_, VERDICT_, ...).

Owner: platform (agents)
Step:  shared
Reads: environment variables (.env locally; see .env.example)
Emits: a Settings base class each folder subclasses
Rules: secrets come only from the environment and are never logged; a missing secret fails with a clear message,
       never a hardcoded fallback; safety constants (thresholds, timeouts, transfer efficiencies) live in one named
       settings object, are logged at startup, and are recorded in each verdict's versions
Tasks: platform-01, platform-03

Not built yet (phase gate: PLANNING).
"""
