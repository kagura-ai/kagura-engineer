from pathlib import Path

from kagura_engineer.config import Config
from kagura_engineer.review import REVIEW_STATUS_EXIT, review_pr
from kagura_engineer.review.envelope import ReviewEnvelope
from kagura_engineer.review.reviewer import ReviewerResult
from kagura_engineer.review.result import Finding, ReviewStatus


def _cfg():
    return Config(
        profile="test", memory_cloud_url="http://x", workspace_id="w", context_id="c"
    )


class _FakeMem:
    def __init__(self, grounding=None):
        self._g = grounding or []

    def load_pinned(self, context_id):
        return ["pinned: TDD"]

    def recall(self, context_id, query, *, k=5):
        return self._g


def _patch_reviewer(monkeypatch, result):
    import kagura_engineer.review as pkg
    monkeypatch.setattr(pkg, "run_reviewer", lambda **kw: result, raising=True)
    monkeypatch.setattr(pkg, "resolve_head", lambda t: t, raising=True)


def test_green_is_ok(monkeypatch, tmp_path):
    _patch_reviewer(monkeypatch, ReviewerResult(0, "", "", ReviewEnvelope(parsed=True, verdict="green")))
    rep = review_pr(_cfg(), "HEAD", base="main", repo_root=tmp_path, memory=_FakeMem())
    assert rep.status is ReviewStatus.OK
    assert rep.verdict == "green"


def test_yellow_is_ok_with_findings(monkeypatch, tmp_path):
    env = ReviewEnvelope(parsed=True, verdict="yellow",
                         summary={"total": 1, "blocking": 0},
                         findings=[Finding("style", "LOW", "a.py", 1, "nit")])
    _patch_reviewer(monkeypatch, ReviewerResult(0, "", "", env))
    rep = review_pr(_cfg(), "HEAD", base="main", repo_root=tmp_path, memory=_FakeMem())
    assert rep.status is ReviewStatus.OK
    assert rep.findings[0].title == "nit"


def test_red_is_blocked(monkeypatch, tmp_path):
    env = ReviewEnvelope(parsed=True, verdict="red",
                         summary={"blocking": 1},
                         findings=[Finding("security", "HIGH", "a.py", 3, "SQLi")])
    _patch_reviewer(monkeypatch, ReviewerResult(1, "", "", env))
    rep = review_pr(_cfg(), "HEAD", base="main", repo_root=tmp_path, memory=_FakeMem())
    assert rep.status is ReviewStatus.BLOCKED
    assert rep.resume_hint is not None


def test_no_changes_is_ok(monkeypatch, tmp_path):
    _patch_reviewer(monkeypatch, ReviewerResult(0, "No changes to review.\n", "",
                                                ReviewEnvelope(parsed=False), no_changes=True))
    rep = review_pr(_cfg(), "HEAD", base="main", repo_root=tmp_path, memory=_FakeMem())
    assert rep.status is ReviewStatus.OK
    assert rep.verdict == "green"


def test_infra_exit_is_fail(monkeypatch, tmp_path):
    _patch_reviewer(monkeypatch, ReviewerResult(2, "", "git diff failed", ReviewEnvelope(parsed=False)))
    rep = review_pr(_cfg(), "HEAD", base="main", repo_root=tmp_path, memory=_FakeMem())
    assert rep.status is ReviewStatus.FAIL


def test_unparseable_envelope_is_fail(monkeypatch, tmp_path):
    _patch_reviewer(monkeypatch, ReviewerResult(0, "garbage", "", ReviewEnvelope(parsed=False)))
    rep = review_pr(_cfg(), "HEAD", base="main", repo_root=tmp_path, memory=_FakeMem())
    assert rep.status is ReviewStatus.FAIL


def test_reviewer_not_on_path_is_fail(monkeypatch, tmp_path):
    import kagura_engineer.review as pkg

    def _boom(**kw):
        raise OSError("kagura-code-reviewer: not found")

    monkeypatch.setattr(pkg, "resolve_head", lambda t: t, raising=True)
    monkeypatch.setattr(pkg, "run_reviewer", _boom, raising=True)
    rep = review_pr(_cfg(), "HEAD", base="main", repo_root=tmp_path, memory=_FakeMem())
    assert rep.status is ReviewStatus.FAIL
    assert "could not" in rep.detail.lower() or "not found" in rep.detail.lower()


def test_recall_failure_is_fail(monkeypatch, tmp_path):
    import kagura_engineer.review as pkg
    monkeypatch.setattr(pkg, "resolve_head", lambda t: t, raising=True)

    class _BadMem(_FakeMem):
        def load_pinned(self, context_id):
            raise RuntimeError("sdk down")

    rep = review_pr(_cfg(), "HEAD", base="main", repo_root=tmp_path, memory=_BadMem())
    assert rep.status is ReviewStatus.FAIL


def test_exit_map():
    assert REVIEW_STATUS_EXIT[ReviewStatus.OK] == 0
    assert REVIEW_STATUS_EXIT[ReviewStatus.FAIL] == 1
    assert REVIEW_STATUS_EXIT[ReviewStatus.BLOCKED] == 2
