"""
Tests for memory_db.py — the module 59 others import.

Focused on normalize_memory_item and the SQL builders, because those are where
a silent mistake corrupts stored data or quietly changes what search returns,
and neither needs a live database to pin.

The episodic and window-filter behaviours are covered here as contracts rather
than as queries: they are the reason the two retrieval lanes exist, and an
accidental edit that drops the window clause would still pass every test that
only checks "rows came back".
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import memory_db  # noqa: E402


# ---------------------------------------------------------------------------
# normalize_memory_item — every write goes through this
# ---------------------------------------------------------------------------

def test_normalize_emits_exactly_the_declared_columns():
    """The INSERT is built from MEMORY_ITEM_COLUMNS; a mismatch breaks writes."""
    row = memory_db.normalize_memory_item({"id": "x", "text": "t"})
    assert set(row) == set(memory_db.MEMORY_ITEM_COLUMNS)


def test_normalize_defaults_status_and_sensitivity():
    """An item with no status must not be written as NULL and vanish."""
    row = memory_db.normalize_memory_item({"id": "x", "text": "t"})
    assert row["status"] == "active"
    # Retrieval filters on sensitivity; NULL would make the row unreachable.
    assert row["sensitivity"] == "public"


def test_normalize_coerces_unparseable_scores_to_zero():
    """Bad confidence must not raise mid-batch and abort an entire upsert."""
    row = memory_db.normalize_memory_item(
        {"id": "x", "text": "t", "confidence": "not-a-number", "importance": None}
    )
    assert row["confidence"] == 0.0
    assert row["importance"] == 0.0


def test_normalize_accepts_retention_priority_as_importance():
    """Older callers emit retention_priority; dropping it loses the ranking."""
    row = memory_db.normalize_memory_item(
        {"id": "x", "text": "t", "retention_priority": 0.9}
    )
    assert row["importance"] == 0.9


def test_normalize_never_writes_null_into_jsonb_columns():
    """NULL in a JSONB column breaks readers that expect a list or object."""
    row = memory_db.normalize_memory_item({"id": "x", "text": "t"})
    for col in memory_db.JSONB_COLUMNS:
        assert row[col] is not None


def test_normalize_zeroes_ranking_adjustments():
    """These are added to a score; NULL would poison the arithmetic."""
    row = memory_db.normalize_memory_item({"id": "x", "text": "t"})
    assert row["ranking_bonus"] == 0
    assert row["ranking_penalty"] == 0


# ---------------------------------------------------------------------------
# Upsert semantics
# ---------------------------------------------------------------------------

def test_upsert_preserves_first_seen_but_advances_last_confirmed():
    """first_seen is when a fact appeared; overwriting it destroys history.

    last_confirmed is the opposite -- it must advance, because recency ranking
    reads it and a frozen value is what made "what did we do yesterday" return
    months-old rows.
    """
    sql = memory_db.UPSERT_SQL
    assert "first_seen = COALESCE(memory_items.first_seen, EXCLUDED.first_seen)" in sql
    assert "last_confirmed = EXCLUDED.last_confirmed" in sql


def test_upsert_touches_updated_at():
    """The Neo4j sync watermark keys off updated_at; a stale value skips rows."""
    assert "updated_at = now()" in memory_db.UPSERT_SQL


# ---------------------------------------------------------------------------
# Retrieval contracts
# ---------------------------------------------------------------------------

def test_hybrid_search_accepts_a_time_window():
    """A window is what separates 'what is true' from 'what happened'."""
    params = inspect.signature(memory_db.hybrid_search_memory_items).parameters
    assert "after_ts" in params and "before_ts" in params
    # None means "no window", so the defaults must not silently filter.
    assert params["after_ts"].default is None
    assert params["before_ts"].default is None


def test_hybrid_search_window_uses_the_same_timestamp_as_ranking():
    """Filtering on one column while ranking by another gives incoherent results."""
    src = inspect.getsource(memory_db.hybrid_search_memory_items)
    coalesce = "COALESCE(m.last_confirmed, m.first_seen, m.created_at)"
    assert src.count(coalesce) >= 3, "window filters and event_at must agree"


def test_recency_defaults_come_from_config():
    """Stating the decay constants twice guarantees they eventually disagree."""
    params = inspect.signature(memory_db.hybrid_search_memory_items).parameters
    assert params["half_life_days"].default is None
    assert params["recency_weight"].default is None


def test_embedding_fetch_can_skip_already_embedded_items():
    """Without this the 90s heartbeat re-embedded ~29k items every cycle."""
    params = inspect.signature(memory_db.fetch_memory_items_for_embedding).parameters
    assert "model_name" in params


# ---------------------------------------------------------------------------
# Episodic lane
# ---------------------------------------------------------------------------

def test_episodes_are_append_only():
    """Deduplicating episodes would erase the repetition that is the signal."""
    src = inspect.getsource(memory_db.record_episode)
    assert "INSERT INTO memory_episodes" in src
    assert "ON CONFLICT" not in src, "episodes must never be deduplicated"


def test_episode_search_orders_newest_first():
    """'What happened' is a recency question, not a relevance one."""
    src = inspect.getsource(memory_db.search_episodes)
    assert "ORDER BY started_at DESC" in src


def test_episode_search_treats_time_as_a_filter():
    """The window must be a WHERE clause, not a scoring term."""
    src = inspect.getsource(memory_db.search_episodes)
    assert "started_at >= %s" in src
    assert "started_at < %s" in src
