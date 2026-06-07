from kagura_engineer.run.local_memory import LocalMemoryClient
from kagura_engineer.run.memory import MemoryClient

CTX = "ctx-a"


def _client(tmp_path):
    return LocalMemoryClient(str(tmp_path / "mem.db"))


def test_satisfies_protocol(tmp_path):
    assert isinstance(_client(tmp_path), MemoryClient)


def test_remember_returns_distinct_ids(tmp_path):
    c = _client(tmp_path)
    a = c.remember(CTX, summary="one", content="x", type="note")
    b = c.remember(CTX, summary="two", content="y", type="note")
    assert a and b and a != b


def test_recall_finds_by_keyword(tmp_path):
    c = _client(tmp_path)
    c.remember(CTX, summary="worktree isolation strategy", content="git worktree per issue", type="note")
    c.remember(CTX, summary="unrelated thing", content="banana", type="note")
    out = c.recall(CTX, "worktree", k=5)
    assert "worktree isolation strategy" in out
    assert "unrelated thing" not in out


def test_recall_ranks_by_term_overlap_and_limits_k(tmp_path):
    c = _client(tmp_path)
    c.remember(CTX, summary="alpha beta gamma", content="", type="note")
    c.remember(CTX, summary="alpha only", content="", type="note")
    c.remember(CTX, summary="nothing", content="", type="note")
    out = c.recall(CTX, "alpha beta", k=1)
    assert out == ["alpha beta gamma"]  # most term overlap wins, limited to 1


def test_recall_empty_when_no_match(tmp_path):
    c = _client(tmp_path)
    c.remember(CTX, summary="apples", content="oranges", type="note")
    assert c.recall(CTX, "zzz_no_such_term") == []


def test_recall_is_context_scoped(tmp_path):
    c = _client(tmp_path)
    c.remember("ctx-a", summary="secret a", content="x", type="note")
    c.remember("ctx-b", summary="secret b", content="x", type="note")
    assert c.recall("ctx-a", "secret") == ["secret a"]


def test_load_pinned_empty_by_default(tmp_path):
    c = _client(tmp_path)
    c.remember(CTX, summary="not pinned", content="x", type="note")
    assert c.load_pinned(CTX) == []


def test_state_roundtrip_and_missing(tmp_path):
    c = _client(tmp_path)
    assert c.get_state(CTX, "run:1") is None
    c.set_state(CTX, "run:1", {"done": True, "pr": "u"})
    assert c.get_state(CTX, "run:1") == {"done": True, "pr": "u"}


def test_set_state_upserts(tmp_path):
    c = _client(tmp_path)
    c.set_state(CTX, "k", {"v": 1})
    c.set_state(CTX, "k", {"v": 2})
    assert c.get_state(CTX, "k") == {"v": 2}


def test_state_is_context_scoped(tmp_path):
    c = _client(tmp_path)
    c.set_state("ctx-a", "k", {"v": "a"})
    c.set_state("ctx-b", "k", {"v": "b"})
    assert c.get_state("ctx-a", "k") == {"v": "a"}


def test_persists_across_instances(tmp_path):
    db = str(tmp_path / "mem.db")
    LocalMemoryClient(db).remember(CTX, summary="durable note", content="x", type="note")
    # a fresh client on the same file sees it
    assert "durable note" in LocalMemoryClient(db).recall(CTX, "durable")


def test_creates_parent_dir(tmp_path):
    nested = tmp_path / "a" / "b" / "mem.db"
    c = LocalMemoryClient(str(nested))
    c.remember(CTX, summary="x", content="y", type="note")
    assert nested.is_file()


def test_recall_detailed_returns_id_summary_pairs(tmp_path):
    c = _client(tmp_path)
    mid = c.remember(CTX, summary="worktree note", content="x", type="note")
    assert c.recall_detailed(CTX, "worktree") == [(mid, "worktree note")]


def test_feedback_raises_recall_rank(tmp_path):
    c = _client(tmp_path)
    a = c.remember(CTX, summary="alpha one", content="", type="note")
    c.remember(CTX, summary="alpha two", content="", type="note")
    # equal match + importance → newer ("alpha two") wins the recency tie-break
    assert c.recall(CTX, "alpha", k=1) == ["alpha two"]
    c.feedback(CTX, a)  # reinforce the older one past the tie-break
    assert c.recall(CTX, "alpha", k=1) == ["alpha one"]


def test_feedback_caps_importance_at_one(tmp_path):
    c = _client(tmp_path)
    mid = c.remember(CTX, summary="x", content="", type="note")
    for _ in range(50):
        c.feedback(CTX, mid)  # must not exceed 1.0 / crash
    assert c.recall(CTX, "x") == ["x"]


def test_feedback_unknown_id_is_noop(tmp_path):
    c = _client(tmp_path)
    c.feedback(CTX, "does-not-exist")  # no row updated, no error
