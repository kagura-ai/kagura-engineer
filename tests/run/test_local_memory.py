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


def test_pin_unpin_drives_load_pinned(tmp_path):
    c = _client(tmp_path)
    a = c.remember(CTX, summary="pin me", content="x", type="note")
    c.remember(CTX, summary="not pinned", content="x", type="note")
    assert c.load_pinned(CTX) == []
    c.pin(CTX, a)
    assert c.load_pinned(CTX) == ["pin me"]
    c.unpin(CTX, a)
    assert c.load_pinned(CTX) == []


def test_recall_tag_filter_keeps_any_match(tmp_path):
    c = _client(tmp_path)
    c.remember(CTX, summary="alpha sec", content="", type="note", tags=["security"])
    c.remember(CTX, summary="alpha perf", content="", type="note", tags=["perf"])
    assert c.recall(CTX, "alpha", tags=["security"]) == ["alpha sec"]
    assert set(c.recall(CTX, "alpha", tags=["security", "perf"])) == {"alpha sec", "alpha perf"}


def test_recall_min_importance_filter(tmp_path):
    c = _client(tmp_path)
    c.remember(CTX, summary="alpha low", content="", type="note")   # importance 0.5
    b = c.remember(CTX, summary="alpha high", content="", type="note")
    c.feedback(CTX, b)  # 0.5 -> 0.6
    assert c.recall(CTX, "alpha", min_importance=0.55) == ["alpha high"]


def test_pin_unknown_id_is_noop(tmp_path):
    c = _client(tmp_path)
    c.pin(CTX, "nope"); c.unpin(CTX, "nope")  # no row, no error
    assert c.load_pinned(CTX) == []


def test_explore_returns_tag_neighbors_excluding_seed(tmp_path):
    c = _client(tmp_path)
    seed = c.remember(CTX, summary="seed", content="", type="note", tags=["auth", "db"])
    c.remember(CTX, summary="neighbor auth", content="", type="note", tags=["auth"])
    c.remember(CTX, summary="unrelated", content="", type="note", tags=["ui"])
    names = [s for _, s in c.explore(CTX, seed)]
    assert "neighbor auth" in names
    assert "unrelated" not in names
    assert "seed" not in names  # excludes the seed itself


def test_explore_unknown_or_untagged_seed_is_empty(tmp_path):
    c = _client(tmp_path)
    assert c.explore(CTX, "nope") == []
    s = c.remember(CTX, summary="no tags", content="", type="note")
    assert c.explore(CTX, s) == []


def test_decay_lowers_importance(tmp_path):
    c = _client(tmp_path)
    c.remember(CTX, summary="alpha", content="", type="note")  # importance 0.5
    assert c.recall(CTX, "alpha", min_importance=0.45) == ["alpha"]
    assert c.decay(CTX, factor=0.5) == 1  # 0.5 -> 0.25
    assert c.recall(CTX, "alpha", min_importance=0.45) == []      # filtered out
    assert c.recall(CTX, "alpha", min_importance=0.2) == ["alpha"]  # still there


def test_decay_keeps_importance_within_unit_interval(tmp_path):
    c = _client(tmp_path)
    a = c.remember(CTX, summary="alpha", content="", type="note")  # 0.5
    c.decay(CTX, factor=3.0)  # 0.5*3 = 1.5 → must clamp to 1.0, not exceed
    # importance is now <=1.0: a feedback bump stays capped, recall still works
    assert c.recall(CTX, "alpha", min_importance=1.0) == ["alpha"]   # exactly 1.0
    c.feedback(CTX, a)  # 1.0 + 0.1 → still capped at 1.0 (no overflow)
    assert c.recall(CTX, "alpha", min_importance=1.0) == ["alpha"]
