"""
engram — a searchable long-term memory for agents.

The package exists because the functionality was previously reachable only by
running individual scripts: consumers had to know which of ~200 files to invoke,
with which flags, and parse stdout. This is the stable surface instead.

    import engram

    engram.search("postgres watermark")          # what do we know about X
    engram.recall(since="2d")                    # what happened recently
    engram.recall(since="3d", query="phase 4")   # that topic, that window
    engram.health()                              # is the store usable

Two lanes, and choosing correctly matters more than any tuning:

`search` queries durable facts, deduplicated by content. It answers "what is
true about X".

`recall` queries activity, never deduplicated, with time as a hard filter. It
answers "what happened, and when". Asking the fact store a time question is the
failure this split exists to prevent -- re-doing work re-asserts facts already
known, so the work collapses into existing rows and the only thing left to match
on is vocabulary, which is how "what did we do yesterday" returns things from
months ago.

The implementation still lives under scripts/; this module is the contract.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

__all__ = ["search", "recall", "health", "parse_since", "__version__"]
__version__ = "0.1.0"

# The internals are not yet a package, so make them importable the same way the
# scripts do. Removing this line is the last step of the move, not the first.
_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if _SCRIPTS.is_dir() and str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

_UNITS = {"h": "hours", "d": "days", "w": "weeks"}

# Loading an embedding model costs ~0.3s, so it is built once per process.
_EMBEDDER: Any = None


def parse_since(value: str | datetime) -> datetime:
    """Accept 'yesterday', '3d', '2w', '12h', an ISO date, or a datetime."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    text = str(value).strip().lower()
    if text in {"today", "day"}:
        return now - timedelta(days=1)
    if text == "yesterday":
        return now - timedelta(days=2)
    if text == "week":
        return now - timedelta(weeks=1)
    m = re.fullmatch(r"(\d+)\s*([hdw])", text)
    if m:
        return now - timedelta(**{_UNITS[m.group(2)]: int(m.group(1))})
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(f"cannot read a time from {value!r} (try: 2d, yesterday, 2026-08-01)")
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _embedder():
    global _EMBEDDER
    if _EMBEDDER is None:
        from sentence_transformers import SentenceTransformer

        import config as cfg

        _EMBEDDER = SentenceTransformer(cfg.EMBEDDING_MODEL, device="cpu")
    return _EMBEDDER


def search(
    query: str,
    *,
    limit: int = 10,
    after: str | datetime | None = None,
    before: str | datetime | None = None,
    status: str = "active",
) -> list[dict]:
    """Search durable facts. Returns rows ordered best-first.

    `after`/`before` hard-filter by event time; they are a window, not a hint.
    """
    from memory_db import hybrid_search_memory_items

    return hybrid_search_memory_items(
        _embedder().encode(query).tolist(),
        query_text=query,
        limit=limit,
        status=status,
        after_ts=parse_since(after) if after else None,
        before_ts=parse_since(before) if before else None,
    )


def recall(
    *,
    since: str | datetime = "1d",
    until: str | datetime | None = None,
    query: str | None = None,
    agent: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """What happened in a time window, newest first.

    Text, when given, narrows within the window rather than competing with it.
    """
    from memory_db import search_episodes

    return search_episodes(
        after_ts=parse_since(since),
        before_ts=parse_since(until) if until else None,
        query_text=query,
        agent=agent,
        limit=limit,
    )


def health() -> dict:
    """Whether the store is usable, and how much of it is searchable."""
    from memory_db import get_conn

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM memory_items WHERE status='active'")
        active = cur.fetchone()[0]
        cur.execute(
            """SELECT count(*) FROM memory_items m
               LEFT JOIN memory_item_embeddings e ON e.memory_id = m.id
               WHERE m.status='active' AND e.memory_id IS NULL"""
        )
        unembedded = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM memory_episodes")
        episodes = cur.fetchone()[0]
    return {
        "active_items": active,
        "unembedded": unembedded,
        # Unembedded items are invisible to search under any ranking, so this
        # is the number that says whether retrieval can actually see the store.
        "searchable": active - unembedded,
        "episodes": episodes,
        "ok": unembedded == 0,
    }
