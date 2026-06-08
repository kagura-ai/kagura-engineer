"""Cloud-primary memory with a local write-ahead log (WAL) failover.

Wraps the real cloud `MemoryClient`. Reads pass straight through (Cloud stays
the primary read path; a read failure propagates so the run's `recall` hard-FAIL
is preserved). Only the run's CRITICAL writes — `remember` (savepoint) and
`set_state` (done/halt markers) — are protected: they try the cloud and, only on
a confirmed failure, append to a durable JSONL WAL. `drain()` (called at run
start) replays the WAL to the cloud so a write that missed during a Cloud outage
lands in the moat on the next run.

Best-effort writes (`feedback`, `pin`, `unpin`) are NOT buffered — losing them on
an outage is acceptable, matching the run's best-effort side-effect policy.

See docs/superpowers/specs/2026-06-08-failover-memory-design.md.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

from .memory import MemoryClient

_log = logging.getLogger(__name__)


def default_wal_path(context_id: str) -> Path:
    """Per-context WAL file under ~/.kagura (the existing kagura convention)."""
    return Path.home() / ".kagura" / "engineer" / "wal" / f"{context_id}.jsonl"


class FailoverMemoryClient:
    """A `MemoryClient` that buffers critical cloud writes to a local WAL."""

    def __init__(self, inner: MemoryClient, wal_path: Path) -> None:
        self._inner = inner
        self._wal_path = Path(wal_path)

    # --- reads: delegate, let failures propagate (Cloud-primary) -------------
    def load_pinned(self, context_id: str) -> list[str]:
        return self._inner.load_pinned(context_id)

    def recall(self, context_id: str, query: str, *, k: int = 5,
               tags: list[str] | None = None, min_importance: float = 0.0) -> list[str]:
        return self._inner.recall(context_id, query, k=k, tags=tags,
                                  min_importance=min_importance)

    def recall_detailed(self, context_id: str, query: str, *, k: int = 5,
                        tags: list[str] | None = None,
                        min_importance: float = 0.0) -> list[tuple[str, str]]:
        return self._inner.recall_detailed(context_id, query, k=k, tags=tags,
                                          min_importance=min_importance)

    def explore(self, context_id: str, memory_id: str, *, depth: int = 1
                ) -> list[tuple[str, str]]:
        return self._inner.explore(context_id, memory_id, depth=depth)

    def get_state(self, context_id: str, key: str) -> dict | None:
        return self._inner.get_state(context_id, key)

    # --- lifecycle -----------------------------------------------------------
    def close(self) -> None:
        closer = getattr(self._inner, "close", None)
        if closer is not None:
            closer()
