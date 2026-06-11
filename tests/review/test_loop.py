import pytest

from kagura_engineer.config import Config
from kagura_engineer.review import loop
from kagura_engineer.review.fixer import FixerResult
from kagura_engineer.review.loop import review_fix_loop
from kagura_engineer.review.result import ReviewReport, ReviewStatus


def _cfg(max_loops=3):
    c = Config(profile="t", memory_cloud_url="http://x", workspace_id="w", context_id="c")
    return c.model_copy(update={"review": c.review.model_copy(update={"max_loops": max_loops})})


def _rep(status, verdict=None):
    return ReviewReport(target="HEAD", base="main", status=status, verdict=verdict,
                        report_path="/tmp/.kagura/review.json")


class _Mem:
    def load_pinned(self, c): return []
    def recall(self, c, q, *, k=5): return []


def _seq_review(monkeypatch, statuses):
    """Make review_pr return the given statuses in order."""
    it = iter(statuses)
    monkeypatch.setattr(loop, "review_pr", lambda *a, **kw: _rep(next(it)), raising=True)


def _ok_fixer(monkeypatch):
    monkeypatch.setattr(loop, "run_fixer", lambda repo, prompt, **kw: FixerResult(0, "fixed", ""), raising=True)


def test_already_clean_no_fix(monkeypatch, tmp_path):
    _seq_review(monkeypatch, [ReviewStatus.OK])
    _ok_fixer(monkeypatch)
    rep = review_fix_loop(_cfg(), "HEAD", base="main", memory=_Mem(), repo_root=tmp_path)
    assert rep.status is ReviewStatus.OK
    assert rep.fixes_attempted == 0
    assert len(rep.iterations) == 1


def test_red_then_fixed(monkeypatch, tmp_path):
    _seq_review(monkeypatch, [ReviewStatus.BLOCKED, ReviewStatus.OK])
    _ok_fixer(monkeypatch)
    rep = review_fix_loop(_cfg(), "HEAD", base="main", memory=_Mem(), repo_root=tmp_path)
    assert rep.status is ReviewStatus.OK
    assert rep.fixes_attempted == 1
    assert len(rep.iterations) == 2


def test_still_red_after_budget(monkeypatch, tmp_path):
    # max_loops=2 → initial + 2 re-reviews = 3 reviews, 2 fixes, still red
    _seq_review(monkeypatch, [ReviewStatus.BLOCKED, ReviewStatus.BLOCKED, ReviewStatus.BLOCKED])
    _ok_fixer(monkeypatch)
    rep = review_fix_loop(_cfg(max_loops=2), "HEAD", base="main", memory=_Mem(), repo_root=tmp_path)
    assert rep.status is ReviewStatus.BLOCKED
    assert rep.fixes_attempted == 2
    assert len(rep.iterations) == 3
    assert rep.resume_hint is not None


def test_fixer_failure_is_fail(monkeypatch, tmp_path):
    _seq_review(monkeypatch, [ReviewStatus.BLOCKED])
    monkeypatch.setattr(loop, "run_fixer", lambda repo, prompt, **kw: FixerResult(1, "", "boom"), raising=True)
    rep = review_fix_loop(_cfg(), "HEAD", base="main", memory=_Mem(), repo_root=tmp_path)
    assert rep.status is ReviewStatus.FAIL
    assert rep.fixes_attempted == 1
    assert "fix" in rep.detail.lower()


def test_fixer_not_on_path_is_fail(monkeypatch, tmp_path):
    _seq_review(monkeypatch, [ReviewStatus.BLOCKED])

    def _boom(repo, prompt, **kw):
        raise OSError("claude: not found")

    monkeypatch.setattr(loop, "run_fixer", _boom, raising=True)
    rep = review_fix_loop(_cfg(), "HEAD", base="main", memory=_Mem(), repo_root=tmp_path)
    assert rep.status is ReviewStatus.FAIL
    assert "could not launch" in rep.detail.lower()


def test_fixer_bad_mcp_config_is_clean_fail(monkeypatch, tmp_path):
    # The codex adapter parses mcp_config itself and raises ValueError on a
    # missing/non-JSON file (reachable with enable_codex_mcp=true and a stale
    # memory_mcp_config) — a clean FAIL, not a traceback out of the loop.
    _seq_review(monkeypatch, [ReviewStatus.BLOCKED])

    def _boom(repo, prompt, **kw):
        raise ValueError("codex mcp_config '/stale' could not be read as JSON")

    monkeypatch.setattr(loop, "run_fixer", _boom, raising=True)
    rep = review_fix_loop(_cfg(), "HEAD", base="main", memory=_Mem(), repo_root=tmp_path)
    assert rep.status is ReviewStatus.FAIL
    assert "could not launch" in rep.detail.lower()


def test_review_fail_does_not_fix(monkeypatch, tmp_path):
    # an infra FAIL must NOT trigger a fix — findings can't be trusted
    calls = {"fix": 0}
    _seq_review(monkeypatch, [ReviewStatus.FAIL])

    def _count(repo, prompt, **kw):
        calls["fix"] += 1
        return FixerResult(0, "", "")

    monkeypatch.setattr(loop, "run_fixer", _count, raising=True)
    rep = review_fix_loop(_cfg(), "HEAD", base="main", memory=_Mem(), repo_root=tmp_path)
    assert rep.status is ReviewStatus.FAIL
    assert rep.fixes_attempted == 0
    assert calls["fix"] == 0


class _ClosableMem(_Mem):
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


# issue #56: a client the loop creates itself must be closed on EVERY exit path,
# or the cloud client's event loop + httpx client hang the process at exit.
@pytest.mark.parametrize(
    ("statuses", "fixer"),
    [
        ([ReviewStatus.OK], None),                                          # clean
        ([ReviewStatus.FAIL], None),                                        # review infra FAIL
        ([ReviewStatus.BLOCKED, ReviewStatus.BLOCKED], None),               # budget exhausted
        ([ReviewStatus.BLOCKED], lambda r, p, **kw: FixerResult(1, "", "boom")),  # fixer FAIL
        ([ReviewStatus.BLOCKED], "oserror"),                                # fixer not on PATH
    ],
)
def test_owned_memory_client_closed_on_every_exit(monkeypatch, tmp_path, statuses, fixer):
    mem = _ClosableMem()
    monkeypatch.setattr(loop, "resolve_memory_client", lambda cfg: mem, raising=True)
    _seq_review(monkeypatch, statuses)
    if fixer == "oserror":
        def _boom(repo, prompt, **kw):
            raise OSError("claude: not found")
        monkeypatch.setattr(loop, "run_fixer", _boom, raising=True)
    elif fixer is not None:
        monkeypatch.setattr(loop, "run_fixer", fixer, raising=True)
    else:
        _ok_fixer(monkeypatch)

    review_fix_loop(_cfg(max_loops=1), "HEAD", base="main", repo_root=tmp_path)
    assert mem.closed == 1


def test_owned_memory_client_closed_when_loop_raises(monkeypatch, tmp_path):
    # exceptions that propagate out of the loop (review infra crash, non-OSError
    # fixer failure) must still close the owned client — try/finally, not just
    # the return paths.
    mem = _ClosableMem()
    monkeypatch.setattr(loop, "resolve_memory_client", lambda cfg: mem, raising=True)

    def _crash(cfg, target, **kw):
        raise RuntimeError("review infra blew up")

    monkeypatch.setattr(loop, "review_pr", _crash, raising=True)
    with pytest.raises(RuntimeError, match="review infra blew up"):
        review_fix_loop(_cfg(), "HEAD", base="main", repo_root=tmp_path)
    assert mem.closed == 1


def test_injected_memory_client_not_closed(monkeypatch, tmp_path):
    _seq_review(monkeypatch, [ReviewStatus.OK])
    _ok_fixer(monkeypatch)
    mem = _ClosableMem()
    review_fix_loop(_cfg(), "HEAD", base="main", memory=mem, repo_root=tmp_path)
    assert mem.closed == 0


def test_owned_memory_close_failure_is_nonfatal(monkeypatch, tmp_path):
    class _ExplodingMem(_Mem):
        def close(self):
            raise RuntimeError("close blew up")

    monkeypatch.setattr(loop, "resolve_memory_client", lambda cfg: _ExplodingMem(), raising=True)
    _seq_review(monkeypatch, [ReviewStatus.OK])
    _ok_fixer(monkeypatch)
    rep = review_fix_loop(_cfg(), "HEAD", base="main", repo_root=tmp_path)
    assert rep.status is ReviewStatus.OK


def test_mcp_config_threads_to_run_fixer(monkeypatch, tmp_path):
    seen = {}
    _seq_review(monkeypatch, [ReviewStatus.BLOCKED, ReviewStatus.OK])

    def _fixer(repo, prompt, *, mcp_config=None, **kw):
        seen["mcp"] = mcp_config
        return FixerResult(0, "", "")

    monkeypatch.setattr(loop, "run_fixer", _fixer, raising=True)
    cfg = _cfg().model_copy(update={"memory_mcp_config": "/tmp/m.json"})
    review_fix_loop(cfg, "HEAD", base="main", memory=_Mem(), repo_root=tmp_path)
    assert seen["mcp"] == "/tmp/m.json"
