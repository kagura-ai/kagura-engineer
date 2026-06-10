import fcntl
import json
import stat
import threading
import time
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


def _mode(path) -> int:
    return stat.S_IMODE(Path(path).stat().st_mode)


def test_wal_file_and_dir_are_private(tmp_path, permissive_umask):
    # Issue #53: the WAL holds memory payloads (remember content, set_state
    # values) — it must never be world-readable, regardless of umask.
    inner = _FakeInner(); inner.fail_writes = True
    wal_path = tmp_path / "wal-dir" / "wal.jsonl"
    c = FailoverMemoryClient(inner, wal_path)
    c.remember("ctx", summary="secret", content="secret", type="savepoint")
    assert _mode(wal_path) == 0o600
    assert _mode(wal_path.parent) == 0o700


def test_preexisting_wal_artifacts_are_retightened(tmp_path, permissive_umask):
    # Upgrade path: a pre-fix version left the WAL dir/file world-readable.
    # mkdir/os.open modes only apply at creation, so the client must chmod /
    # fchmod existing artifacts back to owner-only on the next append.
    wal_dir = tmp_path / "wal-dir"
    wal_dir.mkdir(mode=0o755)
    wal_path = wal_dir / "wal.jsonl"
    wal_path.touch(mode=0o644)
    inner = _FakeInner(); inner.fail_writes = True
    c = FailoverMemoryClient(inner, wal_path)
    c.remember("ctx", summary="secret", content="secret", type="savepoint")
    assert _mode(wal_path) == 0o600
    assert _mode(wal_dir) == 0o700


def test_wal_rewrite_after_partial_drain_stays_private(tmp_path, permissive_umask):
    # drain() rewrites the WAL via a temp file + os.replace; the rewritten
    # file (and the temp file while it exists) must keep owner-only perms.
    inner = _FakeInner(); inner.fail_writes = True
    wal_path = tmp_path / "wal.jsonl"
    c = FailoverMemoryClient(inner, wal_path)
    c.remember("ctx", summary="s1", content="x", type="savepoint")
    c.remember("ctx", summary="s2", content="x", type="savepoint")

    # First replay succeeds, second fails → WAL is rewritten with the tail.
    calls = {"n": 0}
    def flaky_remember(context_id, *, summary, content, type, tags=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("cloud down again")
    inner.remember = flaky_remember

    assert c.drain() == 1
    assert len(_wal_records(wal_path)) == 1   # rewrite actually happened
    assert _mode(wal_path) == 0o600


def test_drain_replays_in_order_and_empties_wal(tmp_path):
    inner = _FakeInner(); inner.fail_writes = True
    c = FailoverMemoryClient(inner, tmp_path / "wal.jsonl")
    c.remember("ctx", summary="s1", content="x", type="savepoint", tags=None)
    c.set_state("ctx", "run:1", {"done": True})
    assert len(_wal_records(tmp_path / "wal.jsonl")) == 2

    inner.fail_writes = False                          # cloud recovers
    replayed = c.drain()
    assert replayed == 2
    assert _wal_records(tmp_path / "wal.jsonl") == []   # WAL emptied
    assert ("remember", "s1") in inner.calls
    assert ("set_state", "run:1") in inner.calls
    # order preserved: remember before set_state
    assert inner.calls.index(("remember", "s1")) < inner.calls.index(("set_state", "run:1"))


def test_drain_partial_failure_keeps_remaining(tmp_path):
    inner = _FakeInner(); inner.fail_writes = True
    c = FailoverMemoryClient(inner, tmp_path / "wal.jsonl")
    c.remember("ctx", summary="s1", content="x", type="savepoint", tags=None)
    c.remember("ctx", summary="s2", content="x", type="savepoint", tags=None)

    # inner accepts the first replay then fails on the second
    calls = {"n": 0}
    def flaky_remember(context_id, *, summary, content, type, tags=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("cloud down again")
        inner.calls.append(("remember", summary))
    inner.remember = flaky_remember

    replayed = c.drain()
    assert replayed == 1
    recs = _wal_records(tmp_path / "wal.jsonl")
    assert len(recs) == 1                               # the second record retained
    assert recs[0]["kwargs"]["summary"] == "s2"


def test_drain_no_wal_is_zero(tmp_path):
    c = FailoverMemoryClient(_FakeInner(), tmp_path / "wal.jsonl")
    assert c.drain() == 0


def test_drain_unknown_op_is_dropped_not_counted(tmp_path):
    """An unknown op is silently dropped: not counted, not retained in WAL, no raise."""
    wal_path = tmp_path / "wal.jsonl"
    # Write one unknown-op record followed by one valid remember record.
    wal_path.write_text(
        json.dumps({"seq": 1, "op": "bogus", "context_id": "c", "kwargs": {}}) + "\n" +
        json.dumps({"seq": 2, "op": "remember", "context_id": "c",
                    "kwargs": {"summary": "s", "content": "x",
                               "type": "savepoint", "tags": None}}) + "\n",
        encoding="utf-8",
    )
    inner = _FakeInner()
    c = FailoverMemoryClient(inner, wal_path)

    replayed = c.drain()

    # Only the known op is counted.
    assert replayed == 1
    # The valid remember was actually applied.
    assert ("remember", "s") in inner.calls
    # WAL is empty — unknown op not retained, valid op consumed.
    assert _wal_records(wal_path) == []


def test_drain_whitespace_only_wal_is_zero(tmp_path):
    """A WAL file containing only blank lines should drain() to 0 without error."""
    wal_path = tmp_path / "wal.jsonl"
    wal_path.write_text("\n\n   \n", encoding="utf-8")
    c = FailoverMemoryClient(_FakeInner(), wal_path)
    assert c.drain() == 0


def test_drain_skips_corrupt_tail_record(tmp_path):
    """A partial final line (crash mid-append) must not abort the whole drain:
    valid records replay, the corrupt tail is dropped."""
    wal_path = tmp_path / "wal.jsonl"
    wal_path.write_text(
        json.dumps({"seq": 1, "op": "remember", "context_id": "c",
                    "kwargs": {"summary": "s", "content": "x",
                               "type": "savepoint", "tags": None}}) + "\n" +
        '{"seq": 2, "op": "rem',                       # truncated mid-write
        encoding="utf-8",
    )
    inner = _FakeInner()
    c = FailoverMemoryClient(inner, wal_path)

    replayed = c.drain()                               # must not raise

    assert replayed == 1
    assert ("remember", "s") in inner.calls
    assert _wal_records(wal_path) == []                # corrupt tail not retained


def test_drain_skips_invalid_utf8_record(tmp_path):
    """A line with undecodable bytes (disk corruption, torn external write) must
    not abort the drain: decoding is lossy per-line, valid records still replay."""
    wal_path = tmp_path / "wal.jsonl"
    valid = json.dumps({"seq": 1, "op": "remember", "context_id": "c",
                        "kwargs": {"summary": "s", "content": "x",
                                   "type": "savepoint", "tags": None}})
    wal_path.write_bytes(valid.encode("utf-8") + b"\n" + b'{"seq": 2, \xff\xfe\n')

    inner = _FakeInner()
    c = FailoverMemoryClient(inner, wal_path)

    replayed = c.drain()                               # must not raise

    assert replayed == 1
    assert ("remember", "s") in inner.calls
    assert _wal_records(wal_path) == []                # corrupt line not retained


def _set_state_record(seq, key):
    return json.dumps({"seq": seq, "op": "set_state", "context_id": "c",
                       "kwargs": {"key": key, "value": {"v": seq}}})


def test_drain_holds_exclusive_lock_across_replay(tmp_path):
    """The sidecar <wal>.lock must be held (LOCK_EX) for the whole
    read→replay→write, so a probe from inside replay cannot acquire it."""
    wal_path = tmp_path / "wal.jsonl"
    wal_path.write_text(_set_state_record(1, "k1") + "\n", encoding="utf-8")
    probe = {}

    class _Probing(_FakeInner):
        def set_state(self, context_id, key, value):
            lock_path = wal_path.with_name(wal_path.name + ".lock")
            with open(lock_path, "w", encoding="utf-8") as f:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    probe["acquired"] = True
                except BlockingIOError:
                    probe["acquired"] = False

    c = FailoverMemoryClient(_Probing(), wal_path)
    assert c.drain() == 1
    assert probe["acquired"] is False


class _BlockingInner(_FakeInner):
    """Inner whose set_state parks on `release` so a test can hold a drain
    mid-replay while racing another WAL user against it."""

    def __init__(self, in_replay: threading.Event, release: threading.Event):
        super().__init__()
        self._in_replay = in_replay
        self._release = release

    def set_state(self, context_id, key, value):
        self.calls.append(("set_state", key))
        self._in_replay.set()
        assert self._release.wait(timeout=5)


def test_concurrent_drains_do_not_duplicate_replay(tmp_path):
    """Two racing drains must replay each WAL record exactly once: the second
    drain waits for the lock, then sees the already-emptied WAL."""
    wal_path = tmp_path / "wal.jsonl"
    wal_path.write_text(_set_state_record(1, "k1") + "\n" +
                        _set_state_record(2, "k2") + "\n", encoding="utf-8")
    in_replay, release = threading.Event(), threading.Event()
    inner = _BlockingInner(in_replay, release)
    t1 = threading.Thread(target=FailoverMemoryClient(inner, wal_path).drain)
    t2 = threading.Thread(target=FailoverMemoryClient(inner, wal_path).drain)

    t1.start()
    assert in_replay.wait(timeout=5)                   # t1 is mid-replay
    t2.start()
    time.sleep(0.3)                # unlocked drain would read the WAL here
    release.set()
    t1.join(timeout=5); t2.join(timeout=5)

    keys = [k for (m, k) in inner.calls if m == "set_state"]
    assert sorted(keys) == ["k1", "k2"]                # no duplicates
    assert _wal_records(wal_path) == []


def test_append_during_drain_is_not_lost(tmp_path):
    """A buffered write racing a drain must survive: drain's WAL rewrite may
    not clobber a record appended by another client."""
    wal_path = tmp_path / "wal.jsonl"
    wal_path.write_text(_set_state_record(1, "k1") + "\n", encoding="utf-8")
    in_replay, release = threading.Event(), threading.Event()
    draining = FailoverMemoryClient(_BlockingInner(in_replay, release), wal_path)
    failing = _FakeInner(); failing.fail_writes = True
    buffering = FailoverMemoryClient(failing, wal_path)

    t1 = threading.Thread(target=draining.drain)
    t1.start()
    assert in_replay.wait(timeout=5)                   # drain is mid-replay
    t2 = threading.Thread(
        target=lambda: buffering.set_state("ctx", "buffered", {"v": 1}))
    t2.start()
    time.sleep(0.3)                # unlocked _append would write here
    release.set()
    t1.join(timeout=5); t2.join(timeout=5)

    recs = _wal_records(wal_path)
    assert [r["kwargs"]["key"] for r in recs] == ["buffered"]


def _cfg(**over):
    from kagura_engineer.config import Config
    base = dict(profile="coding", memory_cloud_url="https://m",
                workspace_id="w", context_id="c")
    base.update(over)
    return Config(**base)


def test_resolve_wraps_cloud_when_failover_on(monkeypatch):
    from kagura_engineer.run import memory as mem_mod
    # Avoid importing the real SDK: stub the cloud client constructor.
    monkeypatch.setattr(mem_mod.KaguraCloudClient, "from_config",
                        classmethod(lambda cls, cfg: _FakeInner()))
    client = mem_mod.resolve_memory_client(_cfg(memory_failover=True))
    assert isinstance(client, FailoverMemoryClient)


def test_resolve_bare_cloud_when_failover_off(monkeypatch):
    from kagura_engineer.run import memory as mem_mod
    fake = _FakeInner()
    monkeypatch.setattr(mem_mod.KaguraCloudClient, "from_config",
                        classmethod(lambda cls, cfg: fake))
    client = mem_mod.resolve_memory_client(_cfg(memory_failover=False))
    assert client is fake                                # not wrapped


def test_resolve_local_unchanged(tmp_path):
    from kagura_engineer.run import memory as mem_mod
    from kagura_engineer.run.local_memory import LocalMemoryClient
    cfg = _cfg(memory_backend="local", local_memory_path=str(tmp_path / "m.db"),
               memory_cloud_url="", workspace_id="", context_id="")
    client = mem_mod.resolve_memory_client(cfg)
    assert isinstance(client, LocalMemoryClient)
