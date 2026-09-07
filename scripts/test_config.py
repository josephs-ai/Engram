"""
Tests for config.py — the module 60 others import.

It had no test despite being the single point that decides where the data lives
and which database everything talks to. Both of those have already gone wrong
here: ~30 scripts read OPENCLAW_MEMORY_DSN while the core data layer read
OPENCLAW_MEMORY_DB_DSN, so two halves of one deployment silently used different
databases; and dozens of scripts rebuilt Path.home()/".openclaw" themselves,
which made OPENCLAW_MEMORY_ROOT relocate the config module and nothing else.

These pin the contracts that prevent both.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import config  # noqa: E402


def test_dsn_prefers_canonical_name(monkeypatch):
    monkeypatch.setenv("OPENCLAW_MEMORY_DSN", "dbname=canonical")
    monkeypatch.setenv("OPENCLAW_MEMORY_DB_DSN", "dbname=legacy")
    assert config.resolve_db_dsn() == "dbname=canonical"


def test_dsn_accepts_legacy_alias(monkeypatch):
    """The live .env uses the legacy name; ignoring it split the deployment."""
    monkeypatch.delenv("OPENCLAW_MEMORY_DSN", raising=False)
    monkeypatch.setenv("OPENCLAW_MEMORY_DB_DSN", "dbname=legacy")
    assert config.resolve_db_dsn() == "dbname=legacy"


def test_blank_canonical_does_not_shadow_alias(monkeypatch):
    """An accidentally-blank export must not silently select the default."""
    monkeypatch.setenv("OPENCLAW_MEMORY_DSN", "")
    monkeypatch.setenv("OPENCLAW_MEMORY_DB_DSN", "dbname=legacy")
    assert config.resolve_db_dsn() == "dbname=legacy"


def test_dsn_falls_back_to_default(monkeypatch):
    monkeypatch.delenv("OPENCLAW_MEMORY_DSN", raising=False)
    monkeypatch.delenv("OPENCLAW_MEMORY_DB_DSN", raising=False)
    assert config.resolve_db_dsn() == config.DEFAULT_DB_DSN


def test_dsn_resolves_at_call_time(monkeypatch):
    """Daemons set the environment after import; a snapshot would miss it."""
    monkeypatch.setenv("OPENCLAW_MEMORY_DSN", "dbname=first")
    first = config.resolve_db_dsn()
    monkeypatch.setenv("OPENCLAW_MEMORY_DSN", "dbname=second")
    assert first == "dbname=first"
    assert config.resolve_db_dsn() == "dbname=second"


def test_memory_root_override_relocates_every_path(monkeypatch):
    """The override must move the whole tree, not just MEMORY_ROOT.

    Scripts that rebuilt these paths from Path.home() ignored the override and
    pinned themselves to one machine's layout.
    """
    monkeypatch.setenv("OPENCLAW_MEMORY_ROOT", "/tmp/relocated-test")
    reloaded = importlib.reload(config)
    try:
        root = Path("/tmp/relocated-test")
        assert reloaded.MEMORY_ROOT == root
        assert reloaded.SCRIPTS_DIR == root / "scripts"
        assert reloaded.WORKSPACE == root.parent
        assert reloaded.OPENCLAW_ROOT == root.parent.parent
        assert reloaded.AGENTS_DIR == root.parent.parent / "agents"
    finally:
        monkeypatch.delenv("OPENCLAW_MEMORY_ROOT", raising=False)
        importlib.reload(config)


def test_paths_are_consistent_with_each_other():
    """The derived paths must describe one tree, not several."""
    assert config.SCRIPTS_DIR.parent == config.MEMORY_ROOT
    assert config.MEMORY_ROOT.parent == config.WORKSPACE
    assert config.WORKSPACE.parent == config.OPENCLAW_ROOT
    assert config.AGENTS_DIR.parent == config.OPENCLAW_ROOT


def test_scoring_weights_are_numeric_and_sane():
    """Ranking is unusable if a weight is missing or non-numeric."""
    for name in ("WEIGHT_FTS", "WEIGHT_VECTOR", "WEIGHT_TERM_IN_TEXT",
                 "WEIGHT_TERM_IN_VALUE", "RECENCY_WEIGHT", "RECENCY_HALF_LIFE_DAYS",
                 "WEIGHT_RERANK_BASE", "WEIGHT_RERANK_SCORE", "GRAPH_HIT_BONUS",
                 "PROMOTION_MIN_CONFIDENCE", "PROMOTION_MIN_IMPORTANCE"):
        value = getattr(config, name)
        assert isinstance(value, float), f"{name} is not a float"
        assert value >= 0, f"{name} is negative"
    # A zero half-life divides by zero in the decay term.
    assert config.RECENCY_HALF_LIFE_DAYS > 0
    # Specific fields should outrank free text, or structured search is pointless.
    assert config.WEIGHT_TERM_IN_VALUE > config.WEIGHT_TERM_IN_TEXT


def test_weights_are_env_overridable(monkeypatch):
    """Tuning ranking per deployment must not require a code change."""
    monkeypatch.setenv("OPENCLAW_WEIGHT_FTS", "7.5")
    reloaded = importlib.reload(config)
    try:
        assert reloaded.WEIGHT_FTS == 7.5
    finally:
        monkeypatch.delenv("OPENCLAW_WEIGHT_FTS", raising=False)
        importlib.reload(config)


def test_malformed_weight_falls_back_rather_than_crashing(monkeypatch):
    """A typo in an env var should not take the whole system down."""
    monkeypatch.setenv("OPENCLAW_WEIGHT_FTS", "not-a-number")
    reloaded = importlib.reload(config)
    try:
        assert reloaded.WEIGHT_FTS == 3.0
    finally:
        monkeypatch.delenv("OPENCLAW_WEIGHT_FTS", raising=False)
        importlib.reload(config)
