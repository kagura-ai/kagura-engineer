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
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .memory import MemoryClient

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows has no fcntl (issue #82)
    fcntl = None

_log = logging.getLogger(__name__)


def _lock_exclusive(fileobj) -> None:
    """Acquire an exclusive cross-process lock on an open file (POSIX `flock`).

    On a platform without `fcntl` (Windows, issue #82) the WAL lock degrades to a
    no-op: it is belt-and-suspenders — the run already discourages two
    kagura-engineer runs in one repo (git-worktree contention) — and the WAL
    writes it serialises are themselves best-effort, so losing cross-process
    exclusion there is acceptable; crashing the import is not. The POSIX branch is
    byte-for-byte the prior behaviour (verified by the Linux-CI lock tests)."""
    if fcntl is not None:
        fcntl.flock(fileobj.fileno(), fcntl.LOCK_EX)


def _unlock(fileobj) -> None:
    """Release the lock taken by `_lock_exclusive` (no-op without `fcntl`)."""
    if fcntl is not None:
        fcntl.flock(fileobj.fileno(), fcntl.LOCK_UN)


def _fchmod_600(fileno: int) -> None:
    """Tighten an open fd to owner-only where it has effect (#53/#82).

    `os.fchmod` is documented Unix-only. On Windows CPython it is currently
    present but a no-op (it does not touch NTFS ACLs); on a build where it is
    absent the `hasattr` guard skips it — either way the WAL write must not crash.
    The #53 owner-only posture therefore holds on Windows via NTFS ACL
    inheritance from the user profile (`~/.kagura/...`), not via POSIX mode bits;
    on POSIX `os.fchmod` applies the 0o600 mode exactly as before."""
    if hasattr(os, "fchmod"):
        os.fchmod(fileno, 0o600)


def default_wal_path(context_id: str) -> Path:
    """Per-context WAL file under ~/.kagura (the existing kagura convention)."""
    return Path.home() / ".kagura" / "engineer" / "wal" / f"{context_id}.jsonl"


class FailoverMemoryClient:
    """A `MemoryClient` that buffers critical cloud writes to a local WAL."""

    def __init__(self, inner: MemoryClient, wal_path: Path) -> None:
        self._inner = inner
        self._wal_path = Path(wal_path)
        self._lock_path = self._wal_path.with_name(self._wal_path.name + ".lock")

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

    # --- WAL locking ----------------------------------------------------------
    @contextmanager
    def _wal_lock(self) -> Generator[None]:
        """Exclusive cross-process lock serialising every WAL read-modify-write
        (`_append` and `drain`). A sidecar `<wal>.lock` file is locked instead of
        the WAL itself because drain unlinks/`os.replace`s the WAL — a lock on
        that inode would silently stop excluding once the path is swapped."""
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._lock_path, "w", encoding="utf-8") as f:
            _lock_exclusive(f)
            try:
                yield
            finally:
                _unlock(f)

    # --- WAL append (best-effort durable) ------------------------------------
    def _append(self, op: str, context_id: str, kwargs: dict[str, Any]) -> None:
        """Durably append one WAL record. Best-effort: a WAL write failure (disk
        full, unwritable dir, non-serialisable value) is logged and swallowed so
        the caller's no-raise contract holds — the write is then truly lost, which
        is the same outcome as a dropped best-effort write."""
        try:
            # The WAL carries memory payloads (remember content, set_state
            # values) — keep the dir owner-only regardless of umask (#53).
            # mkdir's mode only applies at creation, so chmod retroactively
            # tightens a dir left world-readable by a pre-fix version.
            self._wal_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self._wal_path.parent, 0o700)
            with self._wal_lock():
                self._append_locked(op, context_id, kwargs)
        except Exception:  # noqa: BLE001 — WAL buffering is itself best-effort
            _log.exception("WAL append failed; write is lost (op=%s)", op)

    def _append_locked(self, op: str, context_id: str, kwargs: dict[str, Any]) -> None:
        records = self._read_records()
        seq = max((r.get("seq", 0) for r in records), default=0) + 1
        record = {"seq": seq, "op": op, "context_id": context_id, "kwargs": kwargs}
        # Owner-only (0o600) regardless of umask; fchmod retroactively tightens a
        # file left world-readable by a pre-fix version (#53).
        fd = os.open(self._wal_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with open(fd, "a", encoding="utf-8") as f:
            _fchmod_600(f.fileno())
            f.write(json.dumps(record) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def _read_records(self) -> list[dict]:
        """Parse the WAL, skipping undecodable lines (e.g. a partial tail record
        from a crash mid-append) so one corrupt line cannot drop the whole WAL.
        Decoding is lossy (errors="replace") for the same reason: valid records
        are ASCII-only (json.dumps escapes non-ASCII), so replacement characters
        can only land in already-corrupt lines, which then fail json.loads and
        are skipped instead of raising UnicodeDecodeError for the whole file."""
        if not self._wal_path.exists():
            return []
        records: list[dict] = []
        text = self._wal_path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                _log.warning("skipping corrupt WAL record in %s: %.80r",
                             self._wal_path, line)
        return records

    # --- replay (drain) ------------------------------------------------------
    def drain(self) -> int:
        """Replay buffered WAL records to the inner cloud client in order. Drop
        each record on success; stop at the first failure and keep the rest for
        the next drain. Returns the count replayed.

        Holds the WAL lock across the whole read→replay→write so a concurrent
        run cannot replay the same records twice or have its own buffered
        writes clobbered by this drain's rewrite (issue #55)."""
        with self._wal_lock():
            records = self._read_records()
            if not records:
                return 0
            replayed = 0
            remaining: list[dict] = []
            stop = False
            for rec in records:
                if stop:
                    remaining.append(rec)
                    continue
                try:
                    if self._replay(rec):
                        replayed += 1
                except Exception:  # noqa: BLE001 — cloud still down; keep this + rest
                    _log.warning("WAL replay failed at seq %s; %d records retained",
                                 rec.get("seq"), len(records) - replayed)
                    stop = True
                    remaining.append(rec)
            self._write_records(remaining)
            return replayed

    def _write_records(self, records: list[dict]) -> None:
        """Atomically + durably replace the WAL with `records` (empty → remove).
        Write a sibling temp file, fsync it, then os.replace (atomic on POSIX) so
        a crash mid-rewrite cannot truncate the not-yet-replayed tail."""
        if not records:
            self._wal_path.unlink(missing_ok=True)
            return
        tmp = self._wal_path.with_suffix(self._wal_path.suffix + ".tmp")
        # Owner-only like _append — os.replace would otherwise swap the 0600
        # WAL for a umask-default (world-readable) rewrite (#53). fchmod also
        # covers a leftover world-readable .tmp from a crashed pre-fix drain,
        # which O_TRUNC alone would reuse with its old mode.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with open(fd, "w", encoding="utf-8") as f:
            _fchmod_600(f.fileno())
            f.write("\n".join(json.dumps(r) for r in records) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self._wal_path)

    def _replay(self, rec: dict) -> bool:
        """Apply one record to the inner client. Returns True if a known op was
        replayed, False if the op was unknown (dropped for forward-compat)."""
        op, context_id, kwargs = rec["op"], rec["context_id"], rec["kwargs"]
        if op == "remember":
            self._inner.remember(context_id, **kwargs)
            return True
        if op == "set_state":
            self._inner.set_state(context_id, kwargs["key"], kwargs["value"])
            return True
        _log.warning("unknown WAL op %r; dropping record", op)
        return False

    # --- lifecycle -----------------------------------------------------------
    def close(self) -> None:
        closer = getattr(self._inner, "close", None)
        if closer is not None:
            closer()
