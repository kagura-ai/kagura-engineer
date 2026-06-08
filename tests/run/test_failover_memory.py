import json
from pathlib import Path

import pytest

from kagura_engineer.run.failover_memory import FailoverMemoryClient, default_wal_path


class _FakeInner:
    """Fake MemoryClient. Toggle `fail_writes` to make writes raise."""
    def __init__(self):
        self.fail_writes = False
        self.calls = []          # (method, args) actually delegated to inner
        self.closed = False

    # reads
    def load_pinned(self, context_id):
        self.calls.append(("load_pinned", context_id)); return ["pin"]
    def recall(self, context_id, query, *, k=5, tags=None, min_importance=0.0):
        self.calls.append(("recall", query)); return ["r"]
    def recall_detailed(self, context_id, query, *, k=5, tags=None, min_importance=0.0):
        self.calls.append(("recall_detailed", query)); return [("m1", "r")]
    def explore(self, context_id, memory_id, *, depth=1):
        self.calls.append(("explore", memory_id)); return []
    def get_state(self, context_id, key):
        self.calls.append(("get_state", key)); return {"k": key}
    # writes
    def remember(self, context_id, *, summary, content, type, tags=None):
        if self.fail_writes:
            raise RuntimeError("cloud down")
        self.calls.append(("remember", summary)); return "cloud-id"
    def set_state(self, context_id, key, value):
        if self.fail_writes:
            raise RuntimeError("cloud down")
        self.calls.append(("set_state", key))
    def feedback(self, context_id, memory_id, *, weight=1.0):
        if self.fail_writes:
            raise RuntimeError("cloud down")
        self.calls.append(("feedback", memory_id))
    def pin(self, context_id, memory_id):
        self.calls.append(("pin", memory_id))
    def unpin(self, context_id, memory_id):
        self.calls.append(("unpin", memory_id))
    def close(self):
        self.closed = True


def _client(tmp_path) -> FailoverMemoryClient:
    return FailoverMemoryClient(_FakeInner(), tmp_path / "wal.jsonl")


def test_reads_delegate_to_inner(tmp_path):
    c = _client(tmp_path)
    assert c.load_pinned("ctx") == ["pin"]
    assert c.recall("ctx", "q") == ["r"]
    assert c.recall_detailed("ctx", "q") == [("m1", "r")]
    assert c.explore("ctx", "m1") == []
    assert c.get_state("ctx", "run:1") == {"k": "run:1"}
    assert [m for m, _ in c._inner.calls] == [
        "load_pinned", "recall", "recall_detailed", "explore", "get_state",
    ]


def test_close_delegates_to_inner(tmp_path):
    inner = _FakeInner()
    FailoverMemoryClient(inner, tmp_path / "wal.jsonl").close()
    assert inner.closed is True


def test_read_failure_propagates(tmp_path):
    # Cloud-primary invariant: a read failure must NOT be swallowed (recall
    # hard-FAIL must survive).
    class _Boom(_FakeInner):
        def recall(self, *a, **k):
            raise RuntimeError("cloud down")
    c = FailoverMemoryClient(_Boom(), tmp_path / "wal.jsonl")
    with pytest.raises(RuntimeError):
        c.recall("ctx", "q")


def test_default_wal_path_is_under_kagura_and_keyed_by_context(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    p = default_wal_path("ctx-123")
    assert p == tmp_path / ".kagura" / "engineer" / "wal" / "ctx-123.jsonl"
