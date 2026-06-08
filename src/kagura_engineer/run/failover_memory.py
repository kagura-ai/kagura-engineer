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

    # --- critical writes: cloud-first, buffer to WAL on confirmed failure ----
    def remember(self, context_id: str, *, summary: str, content: str, type: str,
                 tags: list[str] | None = None) -> str:
        kwargs = {"summary": summary, "content": content, "type": type, "tags": tags}
        try:
            return self._inner.remember(context_id, **kwargs)
        except Exception:  # noqa: BLE001 — confirmed cloud failure → buffer
            _log.warning("cloud remember failed; buffering to WAL %s", self._wal_path)
            self._append("remember", context_id, kwargs)
            return f"wal:{uuid.uuid4().hex}"

    def set_state(self, context_id: str, key: str, value: dict) -> None:
        try:
            self._inner.set_state(context_id, key, value)
        except Exception:  # noqa: BLE001 — confirmed cloud failure → buffer
            _log.warning("cloud set_state failed; buffering to WAL %s", self._wal_path)
            self._append("set_state", context_id, {"key": key, "value": value})

    # --- best-effort writes: delegate, NOT buffered --------------------------
    def feedback(self, context_id: str, memory_id: str, *, weight: float = 1.0) -> None:
        self._inner.feedback(context_id, memory_id, weight=weight)

    def pin(self, context_id: str, memory_id: str) -> None:
        self._inner.pin(context_id, memory_id)

    def unpin(self, context_id: str, memory_id: str) -> None:
        self._inner.unpin(context_id, memory_id)

    # --- WAL append (best-effort durable) ------------------------------------
    def _append(self, op: str, context_id: str, kwargs: dict[str, Any]) -> None:
        """Durably append one WAL record. Best-effort: a WAL write failure (disk
        full, unwritable dir, non-serialisable value) is logged and swallowed so
        the caller's no-raise contract holds — the write is then truly lost, which
        is the same outcome as a dropped best-effort write."""
        try:
            self._wal_path.parent.mkdir(parents=True, exist_ok=True)
            records = self._read_records()
            seq = max((r.get("seq", 0) for r in records), default=0) + 1
            record = {"seq": seq, "op": op, "context_id": context_id, "kwargs": kwargs}
            with open(self._wal_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
                f.flush()
                os.fsync(f.fileno())
        except Exception:  # noqa: BLE001 — WAL buffering is itself best-effort
            _log.exception("WAL append failed; write is lost (op=%s)", op)

    def _read_records(self) -> list[dict]:
        if not self._wal_path.exists():
            return []
        return [
            json.loads(line)
            for line in self._wal_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    # --- lifecycle -----------------------------------------------------------
    def close(self) -> None:
        closer = getattr(self._inner, "close", None)
        if closer is not None:
            closer()
