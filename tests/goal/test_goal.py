import subprocess

import kagura_engineer.goal as g
from kagura_engineer.config import Config
from kagura_engineer.goal import run_milestone
from kagura_engineer.run.result import PhaseResult, RunReport, RunStatus


def _cfg():
    return Config(profile="t", memory_cloud_url="http://x", workspace_id="w", context_id="c")


def _rr(issue, status, pr=None):
    phases = [] if status is RunStatus.OK else [PhaseResult("act", status, "x")]
    return RunReport(issue=issue, phases=phases, pr_url=pr)


class _Mem:
    def load_pinned(self, c): return []
    def recall(self, c, q, *, k=5): return []
    def remember(self, c, **k): return "id"
    def get_state(self, c, k): return None
    def set_state(self, c, k, v): return None


def _patch(monkeypatch, issues, run_results):
    monkeypatch.setattr(g, "list_milestone_issues", lambda m: issues, raising=True)
    monkeypatch.setattr(g, "resolve_memory_client", lambda cfg: _Mem(), raising=True)
    it = iter(run_results)
    calls = []

    def _fake_run_idea(cfg, issue, **kw):
        calls.append(issue)
        return next(it)

    monkeypatch.setattr(g, "run_idea", _fake_run_idea, raising=True)
    return calls


def test_all_issues_shipped_is_ok(monkeypatch):
    calls = _patch(monkeypatch, [1, 2, 3],
                   [_rr(1, RunStatus.OK, "u1"), _rr(2, RunStatus.OK, "u2"), _rr(3, RunStatus.OK, "u3")])
    rep = run_milestone(_cfg(), "v0.3")
    assert rep.status is RunStatus.OK
    assert rep.completed == 3
    assert calls == [1, 2, 3]


def test_halts_at_first_blocked(monkeypatch):
    calls = _patch(monkeypatch, [1, 2, 3],
                   [_rr(1, RunStatus.OK, "u1"), _rr(2, RunStatus.BLOCKED), _rr(3, RunStatus.OK)])
    rep = run_milestone(_cfg(), "v0.3")
    assert rep.status is RunStatus.BLOCKED
    assert rep.completed == 1
    assert len(rep.issues) == 2          # #3 was never attempted
    assert calls == [1, 2]
    assert rep.resume_hint is not None and "#2" in rep.resume_hint


def test_halts_at_fail(monkeypatch):
    _patch(monkeypatch, [5], [_rr(5, RunStatus.FAIL)])
    rep = run_milestone(_cfg(), "m")
    assert rep.status is RunStatus.FAIL
    assert rep.completed == 0


def test_no_open_issues_is_ok(monkeypatch):
    _patch(monkeypatch, [], [])
    rep = run_milestone(_cfg(), "empty")
    assert rep.status is RunStatus.OK
    assert rep.issues == []
    assert "no open issues" in rep.detail


def test_gh_failure_is_fail(monkeypatch):
    def _boom(m):
        raise subprocess.CalledProcessError(1, ["gh"])

    monkeypatch.setattr(g, "list_milestone_issues", _boom, raising=True)
    rep = run_milestone(_cfg(), "m")
    assert rep.status is RunStatus.FAIL
    assert "could not list" in rep.detail.lower()


def test_shared_memory_client_across_issues(monkeypatch):
    seen = []
    monkeypatch.setattr(g, "list_milestone_issues", lambda m: [1, 2], raising=True)
    sentinel = _Mem()
    monkeypatch.setattr(g, "resolve_memory_client", lambda cfg: sentinel, raising=True)

    def _fake_run_idea(cfg, issue, *, memory=None, **kw):
        seen.append(memory)
        return _rr(issue, RunStatus.OK)

    monkeypatch.setattr(g, "run_idea", _fake_run_idea, raising=True)
    run_milestone(_cfg(), "m")
    assert seen == [sentinel, sentinel]  # resolved once, reused


def test_unattended_threads_to_run_idea(monkeypatch):
    seen = []
    monkeypatch.setattr(g, "list_milestone_issues", lambda m: [1], raising=True)
    monkeypatch.setattr(g, "resolve_memory_client", lambda cfg: _Mem(), raising=True)

    def _fake_run_idea(cfg, issue, *, unattended=False, **kw):
        seen.append(unattended)
        return _rr(issue, RunStatus.OK)

    monkeypatch.setattr(g, "run_idea", _fake_run_idea, raising=True)
    run_milestone(_cfg(), "m", unattended=True)
    assert seen == [True]
