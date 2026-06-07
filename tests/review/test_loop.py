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
