"""Offline SQLite implementation of the `MemoryClient` Protocol (Plan 5).

`LocalMemoryClient` is the no-network counterpart to `KaguraCloudClient`: it
satisfies the same narrow Protocol the `run`/`review` loops depend on, backed
by a local SQLite file (stdlib `sqlite3` — no new dependency, no API key). It
lets the harness run grounded fully offline; `resolve_memory_client` (in
memory.py) picks it when `cfg.memory_backend == "local"`.

Recall is a deliberately simple keyword overlap score (no embeddings offline):
rank by how many query terms appear in summary+content, then importance, then
recency. `load_pinned` returns explicitly-pinned rows; the narrow Protocol's
`remember` cannot pin, so offline it is empty by design (pinning is a Cloud
feature). Rich graph/feedback/Sleep features stay Cloud-only (Plan 5+).
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id         TEXT PRIMARY KEY,
    context_id TEXT NOT NULL,
    summary    TEXT NOT NULL,
    content    TEXT NOT NULL,
    type       TEXT NOT NULL,
    tags       TEXT NOT NULL DEFAULT '[]',
    importance REAL NOT NULL DEFAULT 0.5,
    pinned     INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS state (
    context_id TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    PRIMARY KEY (context_id, key)
);
"""


class LocalMemoryClient:
    """SQLite-backed MemoryClient. One connection per instance; writes commit
    immediately so a fresh client on the same file sees prior data."""

    def __init__(self, db_path: str) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        # Wait (not error) if another single-run process holds the write lock.
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def load_pinned(self, context_id: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT summary FROM memories WHERE context_id = ? AND pinned = 1 "
            "ORDER BY importance DESC, rowid DESC",
            (context_id,),
        ).fetchall()
        return [r[0] for r in rows]

    def recall(self, context_id: str, query: str, *, k: int = 5) -> list[str]:
        return [s for _, s in self.recall_detailed(context_id, query, k=k)]

    def recall_detailed(
        self, context_id: str, query: str, *, k: int = 5
    ) -> list[tuple[str, str]]:
        # Distinct query terms; substring match (so "cat" also hits "category")
        # is intentional for a simple offline heuristic. Deduped so a repeated
        # term cannot inflate the overlap score.
        terms = {t for t in query.lower().split() if t}
        rows = self._conn.execute(
            "SELECT id, summary, content, importance FROM memories WHERE context_id = ? "
            "ORDER BY rowid DESC",
            (context_id,),
        ).fetchall()
        scored: list[tuple[int, float, str, str]] = []
        for mem_id, summary, content, importance in rows:
            hay = f"{summary}\n{content}".lower()
            score = sum(1 for t in terms if t in hay)
            if terms and score == 0:
                continue  # query given but nothing matched → drop
            scored.append((score, importance, mem_id, summary))
        # rows arrived recency-first; a stable sort by (score, importance) keeps
        # recency as the final tie-breaker. feedback() raises importance, so
        # reinforced memories rank higher on later recalls (Hebbian-ish).
        scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
        return [(s[2], s[3]) for s in scored[:k]]

    def feedback(self, context_id: str, memory_id: str, *, weight: float = 1.0) -> None:
        # Reinforce: nudge importance toward 1.0 (capped). Importance is a
        # recall tie-breaker, so reinforced memories surface earlier next time.
        self._conn.execute(
            "UPDATE memories SET importance = MAX(0.0, MIN(1.0, importance + ?)) "
            "WHERE id = ? AND context_id = ?",
            (0.1 * weight, memory_id, context_id),
        )
        self._conn.commit()

    def remember(
        self, context_id: str, *, summary: str, content: str, type: str,
        tags: list[str] | None = None,
    ) -> str:
        mem_id = uuid.uuid4().hex
        self._conn.execute(
            "INSERT INTO memories (id, context_id, summary, content, type, tags, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (mem_id, context_id, summary, content, type,
             json.dumps(tags or []), datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()
        return mem_id

    def get_state(self, context_id: str, key: str) -> dict | None:
        row = self._conn.execute(
            "SELECT value FROM state WHERE context_id = ? AND key = ?",
            (context_id, key),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def set_state(self, context_id: str, key: str, value: dict) -> None:
        self._conn.execute(
            "INSERT INTO state (context_id, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(context_id, key) DO UPDATE SET value = excluded.value",
            (context_id, key, json.dumps(value)),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
