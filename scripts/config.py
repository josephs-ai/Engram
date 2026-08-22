"""
config.py — Centralized configuration for the OpenClaw Memory System.

All path and connection defaults are resolved here. Scripts should import
from this module instead of hardcoding paths.

Environment variables:
    OPENCLAW_MEMORY_ROOT   — Root of the memory index (default: parent of scripts/)
    OPENCLAW_MEMORY_DSN    — PostgreSQL connection string
    OPENCLAW_MEMORY_DB_DSN — Alias for OPENCLAW_MEMORY_DSN (legacy compat)
    QDRANT_HOST            — Qdrant server host (default: localhost)
    QDRANT_PORT            — Qdrant server port (default: 6333)
    NEO4J_URI              — Neo4j bolt URI (default: bolt://localhost:7687)
    NEO4J_USER             — Neo4j username (default: neo4j)
    NEO4J_PASSWORD         — Neo4j password (default: neo4jpassword)
    OPENCLAW_RERANK_MODEL  — Cross-encoder model name
    OPENCLAW_RERANK_CAP    — Max items for cross-encoder reranking (default: 20)

    Retrieval scoring weights (all optional, see the Scoring section below):
    OPENCLAW_WEIGHT_FTS, OPENCLAW_WEIGHT_VECTOR, OPENCLAW_WEIGHT_TERM_*,
    OPENCLAW_WEIGHT_IMPORTANCE, OPENCLAW_RECENCY_HALF_LIFE_DAYS,
    OPENCLAW_RECENCY_WEIGHT, OPENCLAW_WEIGHT_RERANK_*, OPENCLAW_GRAPH_HIT_BONUS,
    OPENCLAW_WEIGHT_FEEDBACK_BOOST, OPENCLAW_PROMOTION_MIN_*
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

# Default: memory index root is the parent of the scripts/ directory
_SCRIPT_DIR = Path(__file__).resolve().parent
_DEFAULT_ROOT = _SCRIPT_DIR.parent

MEMORY_ROOT = Path(
    os.environ.get("OPENCLAW_MEMORY_ROOT", str(_DEFAULT_ROOT))
).resolve()

SCRIPTS_DIR = MEMORY_ROOT / "scripts"
HEALTH_DIR = MEMORY_ROOT / "health"
CONFIG_JSON = MEMORY_ROOT / "config.json"

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

# Single canonical default. Bare DSN (no user/password) so it works against a
# local Postgres via peer/socket auth; CI and containers must supply a full DSN
# via the env vars below. NOTE: peer auth is why the bare default historically
# "worked" locally despite the env-var-name split — that masked the drift.
DEFAULT_DB_DSN = "dbname=openclaw_memory"


def resolve_db_dsn() -> str:
    """Resolve the PostgreSQL DSN from the environment at CALL time.

    Accepts BOTH historical env var names so a value set under either name is
    honored uniformly across every module (this is the fix for the env-var
    drift where ~30 scripts read OPENCLAW_MEMORY_DSN while the core data layer
    read OPENCLAW_MEMORY_DB_DSN, causing silent identity divergence):

        1. OPENCLAW_MEMORY_DSN     (canonical)
        2. OPENCLAW_MEMORY_DB_DSN  (legacy alias, e.g. live .env)

    Resolving at call time (not import time) lets daemons/tests that set the
    env after import still pick up the correct value. The first non-empty
    value wins; falls back to DEFAULT_DB_DSN. Empty-string values are ignored
    so an accidentally-blank export doesn't shadow the alias.
    """
    return (
        os.environ.get("OPENCLAW_MEMORY_DSN")
        or os.environ.get("OPENCLAW_MEMORY_DB_DSN")
        or DEFAULT_DB_DSN
    )


# Module-level convenience (import-time snapshot). Prefer resolve_db_dsn() in
# code paths that may run before the environment is fully populated.
DB_DSN = resolve_db_dsn()

# ---------------------------------------------------------------------------
# Qdrant
# ---------------------------------------------------------------------------

QDRANT_HOST = os.environ.get("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.environ.get("QDRANT_PORT", "6333"))

# ---------------------------------------------------------------------------
# Neo4j
# ---------------------------------------------------------------------------

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "neo4jpassword")

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

EMBEDDING_MODEL = os.environ.get(
    "OPENCLAW_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
)
RERANK_MODEL = os.environ.get(
    "OPENCLAW_RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2"
)
RERANK_CAP = int(os.environ.get("OPENCLAW_RERANK_CAP", "20"))


# ---------------------------------------------------------------------------
# Retrieval scoring weights
# ---------------------------------------------------------------------------
#
# These were literals scattered across memory_db.py and search_memory.py with
# no explanation, which made the ranking impossible to reason about or tune
# without reading SQL. They are gathered here with what each one actually does.
# Every value is env-overridable so ranking can be tuned without a code change.
#
# The relative sizes matter more than the absolute ones: scores from different
# sources (FTS rank, cosine similarity, term-overlap counts) are on different
# scales, and these weights are what put them on comparable footing.


def _f(name: str, default: float) -> float:
    """Read a float weight from the environment, falling back to the default."""
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# --- Hybrid fusion (memory_db.hybrid_search_memory_items) ---
# ts_rank_cd returns small values (~0-1); cosine similarity is ~0-1 too, but
# lexical agreement is a stronger signal of intent here, so FTS is weighted up.
WEIGHT_FTS = _f("OPENCLAW_WEIGHT_FTS", 3.0)
WEIGHT_VECTOR = _f("OPENCLAW_WEIGHT_VECTOR", 1.1)

# --- Structured-field term overlap ---
# A query term matching a structured field is far more meaningful than the same
# term appearing in free text, and value/property are the most specific of all.
WEIGHT_TERM_IN_TEXT = _f("OPENCLAW_WEIGHT_TERM_TEXT", 0.18)
WEIGHT_TERM_IN_ENTITY = _f("OPENCLAW_WEIGHT_TERM_ENTITY", 0.20)
WEIGHT_TERM_IN_PROPERTY = _f("OPENCLAW_WEIGHT_TERM_PROPERTY", 0.45)
WEIGHT_TERM_IN_VALUE = _f("OPENCLAW_WEIGHT_TERM_VALUE", 0.55)

# Importance contribution. Kept small: importance says how much a fact matters
# in general, not how well it answers this particular query.
WEIGHT_IMPORTANCE = _f("OPENCLAW_WEIGHT_IMPORTANCE", 0.25)

# --- Recency decay (temporal ranking) ---
# Half-life in days for the exponential decay term, and how heavily that term
# counts. Set RECENCY_WEIGHT to 0 to rank purely on relevance.
RECENCY_HALF_LIFE_DAYS = _f("OPENCLAW_RECENCY_HALF_LIFE_DAYS", 30.0)
RECENCY_WEIGHT = _f("OPENCLAW_RECENCY_WEIGHT", 0.6)

# --- Cross-encoder rerank blending (search_memory) ---
# The reranker sees the query and document together and is the better judge, so
# it outweighs the retrieval score rather than replacing it -- keeping some of
# the original score stops a single reranker misfire from burying a good hit.
WEIGHT_RERANK_BASE = _f("OPENCLAW_WEIGHT_RERANK_BASE", 0.35)
WEIGHT_RERANK_SCORE = _f("OPENCLAW_WEIGHT_RERANK_SCORE", 1.25)

# --- Graph and feedback ---
# Flat bonus for an item the knowledge graph also returned: corroboration from
# a second source, not a measure of how relevant it is.
GRAPH_HIT_BONUS = _f("OPENCLAW_GRAPH_HIT_BONUS", 0.20)
# Retrieval feedback is a weak signal from few samples, so it nudges only.
WEIGHT_FEEDBACK_BOOST = _f("OPENCLAW_WEIGHT_FEEDBACK_BOOST", 0.15)

# --- Promotion gates (auto_promote_safe_items) ---
# Policy, not ranking: how good a candidate must be to enter durable memory
# without review. Trades recall against unreviewed prose reaching the store.
PROMOTION_MIN_CONFIDENCE = _f("OPENCLAW_PROMOTION_MIN_CONFIDENCE", 0.85)
PROMOTION_MIN_IMPORTANCE = _f("OPENCLAW_PROMOTION_MIN_IMPORTANCE", 0.85)

# ---------------------------------------------------------------------------
# Config file loader (for legacy scripts that read config.json)
# ---------------------------------------------------------------------------

_config_cache: dict | None = None


def load_config() -> dict:
    """Load config.json from memory root. Returns empty dict if not found."""
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    if CONFIG_JSON.exists():
        _config_cache = json.loads(CONFIG_JSON.read_text(encoding="utf-8"))
    else:
        _config_cache = {}
    return _config_cache
