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


def _wal_records(path):
    if not Path(path).exists():
        return []
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def test_remember_success_does_not_buffer(tmp_path):
    inner = _FakeInner()
    c = FailoverMemoryClient(inner, tmp_path / "wal.jsonl")
    rid = c.remember("ctx", summary="s", content="x", type="savepoint", tags=["t"])
    assert rid == "cloud-id"
    assert _wal_records(tmp_path / "wal.jsonl") == []


def test_remember_failure_buffers_and_returns_wal_id(tmp_path):
    inner = _FakeInner(); inner.fail_writes = True
    c = FailoverMemoryClient(inner, tmp_path / "wal.jsonl")
    rid = c.remember("ctx", summary="s", content="x", type="savepoint", tags=["t"])
    assert rid.startswith("wal:")                      # synthetic id, no raise
    recs = _wal_records(tmp_path / "wal.jsonl")
    assert len(recs) == 1
    assert recs[0]["op"] == "remember"
    assert recs[0]["context_id"] == "ctx"
    assert recs[0]["kwargs"] == {"summary": "s", "content": "x",
                                 "type": "savepoint", "tags": ["t"]}


def test_set_state_failure_buffers(tmp_path):
    inner = _FakeInner(); inner.fail_writes = True
    c = FailoverMemoryClient(inner, tmp_path / "wal.jsonl")
    c.set_state("ctx", "run:1", {"done": True})        # must not raise
    recs = _wal_records(tmp_path / "wal.jsonl")
    assert len(recs) == 1
    assert recs[0]["op"] == "set_state"
    assert recs[0]["kwargs"] == {"key": "run:1", "value": {"done": True}}


def test_feedback_failure_is_not_buffered(tmp_path):
    inner = _FakeInner(); inner.fail_writes = True
    c = FailoverMemoryClient(inner, tmp_path / "wal.jsonl")
    with pytest.raises(RuntimeError):                   # best-effort: propagates
        c.feedback("ctx", "m1")
    assert _wal_records(tmp_path / "wal.jsonl") == []   # never buffered


def test_set_state_success_does_not_buffer(tmp_path):
    inner = _FakeInner()
    c = FailoverMemoryClient(inner, tmp_path / "wal.jsonl")
    c.set_state("ctx", "run:1", {"done": True})
    assert _wal_records(tmp_path / "wal.jsonl") == []


def test_pin_unpin_not_buffered_and_propagate(tmp_path):
    class _RaisingInner(_FakeInner):
        def pin(self, context_id, memory_id):
            raise RuntimeError("cloud down")
        def unpin(self, context_id, memory_id):
            raise RuntimeError("cloud down")

    c = FailoverMemoryClient(_RaisingInner(), tmp_path / "wal.jsonl")
    with pytest.raises(RuntimeError):
        c.pin("ctx", "m1")
    assert _wal_records(tmp_path / "wal.jsonl") == []

    with pytest.raises(RuntimeError):
        c.unpin("ctx", "m1")
    assert _wal_records(tmp_path / "wal.jsonl") == []


def test_append_failure_preserves_no_raise(tmp_path):
    # Make _wal_path's parent a FILE so mkdir raises → _append fails internally.
    blocker = tmp_path / "blocker"
    blocker.write_text("I am a file, not a dir")
    wal_path = blocker / "wal.jsonl"  # mkdir will fail: parent is a file

    inner = _FakeInner(); inner.fail_writes = True
    c = FailoverMemoryClient(inner, wal_path)
    # Even though the WAL write fails, remember must NOT raise.
    rid = c.remember("ctx", summary="s", content="x", type="savepoint")
    assert rid.startswith("wal:")
