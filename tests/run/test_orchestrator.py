from pathlib import Path

from kagura_engineer.doctor.result import CheckResult, Status
from kagura_engineer.run import run_idea, STATUS_EXIT
from kagura_engineer.run.result import RunStatus
from kagura_engineer.run.workflow import PhaseInvocation
from tests._constants import (
    VALID_CONTEXT_UUID, VALID_MEMORY_URL, VALID_PROFILE, VALID_WORKSPACE,
)
from kagura_engineer.config import Config


def _cfg() -> Config:
    return Config(
        profile=VALID_PROFILE, memory_cloud_url=VALID_MEMORY_URL,
        workspace_id=VALID_WORKSPACE, context_id=VALID_CONTEXT_UUID,
    )


class _FakeMemory:
    def __init__(self):
        self.state = {}
        self.remembered = []
        self.feedback_calls = []

    def load_pinned(self, context_id): return ["guardrail: TDD"]
    def recall(self, context_id, query, *, k=5): return ["decision A"]
    def recall_detailed(self, context_id, query, *, k=5): return [("m1", "decision A")]
    def feedback(self, context_id, memory_id, *, weight=1.0):
        self.feedback_calls.append(memory_id)
    def remember(self, context_id, *, summary, content, type, tags=None):
        self.remembered.append((type, summary)); return "mem-1"
    def get_state(self, context_id, key): return self.state.get(key)
    def set_state(self, context_id, key, value): self.state[key] = value


def _patch_boundaries(monkeypatch, *, blocking=False, phases=None):
    """Patch guard/worktree/workflow. `phases` maps phase->PhaseInvocation."""
    checks = [CheckResult("gh-issue-driven", Status.FAIL if blocking else Status.OK, "x")]
    monkeypatch.setattr("kagura_engineer.run.run_all", lambda cfg: checks)
    monkeypatch.setattr("kagura_engineer.run.ensure_worktree", lambda root, issue, base="HEAD": Path(f"/wt/run-{issue}"))
    phases = phases or {}

    def _invoke(phase, issue, worktree, grounding, **kw):
        return phases[phase]

    monkeypatch.setattr("kagura_engineer.run.invoke_phase", _invoke)


def test_status_exit_map():
    assert STATUS_EXIT[RunStatus.OK] == 0
    assert STATUS_EXIT[RunStatus.FAIL] == 1
    assert STATUS_EXIT[RunStatus.BLOCKED] == 2


def test_guard_blocks_when_doctor_has_blocking_fail(monkeypatch):
    _patch_boundaries(monkeypatch, blocking=True)
    mem = _FakeMemory()
    report = run_idea(_cfg(), 42, memory=mem, repo_root=Path("/repo"))
    assert report.status is RunStatus.BLOCKED
    assert report.phases[0].name == "guard"
    assert "setup" in report.resume_hint.lower()
    assert mem.remembered == []  # never got to act/persist


def test_happy_path_reaches_pr_and_persists(monkeypatch):
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 0, "", "", "green", None),
        "ship": PhaseInvocation("ship", 0, "", "", "green", "https://x/pull/9"),
    })
    mem = _FakeMemory()
    report = run_idea(_cfg(), 42, memory=mem, repo_root=Path("/repo"))
    assert report.status is RunStatus.OK
    assert report.pr_url == "https://x/pull/9"
    assert [p.name for p in report.phases] == ["guard", "recall", "worktree", "start", "ship", "persist"]
    assert any(t == "savepoint" for t, _ in mem.remembered)
    assert mem.state.get("run:42") is not None  # resume marker set to done


def test_red_verdict_at_start_halts_and_sets_resume(monkeypatch):
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 0, "", "", "red", None),
    })
    mem = _FakeMemory()
    report = run_idea(_cfg(), 42, memory=mem, repo_root=Path("/repo"))
    assert report.status is RunStatus.BLOCKED
    assert report.phases[-1].name == "start"
    assert report.phases[-1].verdict == "red"
    assert mem.state.get("run:42") is not None  # resume state persisted
    assert "run 42" in report.resume_hint


def test_phase_nonzero_returncode_is_fail(monkeypatch):
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 1, "", "boom", None, None),
    })
    report = run_idea(_cfg(), 42, memory=_FakeMemory(), repo_root=Path("/repo"))
    assert report.status is RunStatus.FAIL
    assert report.phases[-1].name == "start"


def test_no_remember_skips_persist(monkeypatch):
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 0, "", "", "green", None),
        "ship": PhaseInvocation("ship", 0, "", "", "green", "https://x/pull/9"),
    })
    mem = _FakeMemory()
    report = run_idea(_cfg(), 42, memory=mem, no_remember=True, repo_root=Path("/repo"))
    assert report.status is RunStatus.OK
    assert mem.remembered == []  # recall still happened, persist skipped
    assert "persist" not in [p.name for p in report.phases]


def test_worktree_error_is_fail(monkeypatch):
    from kagura_engineer.run.worktree import WorktreeError

    _patch_boundaries(monkeypatch, phases={})

    def _boom(root, issue, base="HEAD"):
        raise WorktreeError("git worktree add failed")

    monkeypatch.setattr("kagura_engineer.run.ensure_worktree", _boom)
    report = run_idea(_cfg(), 42, memory=_FakeMemory(), repo_root=Path("/repo"))
    assert report.status is RunStatus.FAIL
    assert report.phases[-1].name == "worktree"


def test_recall_error_is_fail(monkeypatch):
    _patch_boundaries(monkeypatch, phases={})

    class _BrokenMemory(_FakeMemory):
        def recall_detailed(self, context_id, query, *, k=5):
            raise RuntimeError("kagura connection refused")

    report = run_idea(_cfg(), 42, memory=_BrokenMemory(), repo_root=Path("/repo"))
    assert report.status is RunStatus.FAIL
    assert report.phases[-1].name == "recall"


def test_persist_failure_is_non_fatal(monkeypatch):
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 0, "", "", "green", None),
        "ship": PhaseInvocation("ship", 0, "", "", "green", "https://x/pull/9"),
    })

    class _BrokenPersist(_FakeMemory):
        def remember(self, context_id, *, summary, content, type, tags=None):
            raise RuntimeError("kagura write failed")

    report = run_idea(_cfg(), 42, memory=_BrokenPersist(), repo_root=Path("/repo"))
    assert report.status is RunStatus.OK  # PR was created; persist failure is non-fatal
    assert report.pr_url == "https://x/pull/9"
    assert report.phases[-1].name == "persist"
    assert "failed" in report.phases[-1].detail


def test_phase_timeout_is_fail(monkeypatch):
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", -1, "", "timed out", None, None, timed_out=True),
    })
    report = run_idea(_cfg(), 42, memory=_FakeMemory(), repo_root=Path("/repo"))
    assert report.status is RunStatus.FAIL
    assert "timed out" in report.phases[-1].detail


def test_phase_launch_oserror_is_fail(monkeypatch):
    _patch_boundaries(monkeypatch, phases={})

    def _boom(phase, issue, worktree, grounding, **kw):
        raise OSError("claude: command not found")

    monkeypatch.setattr("kagura_engineer.run.invoke_phase", _boom)
    report = run_idea(_cfg(), 42, memory=_FakeMemory(), repo_root=Path("/repo"))
    assert report.status is RunStatus.FAIL
    assert report.phases[-1].name == "start"


def test_unattended_threads_to_invoke_phase(monkeypatch):
    seen = []
    monkeypatch.setattr("kagura_engineer.run.run_all",
                        lambda cfg: [CheckResult("gh-issue-driven", Status.OK, "x")])
    monkeypatch.setattr("kagura_engineer.run.ensure_worktree",
                        lambda root, issue, base="HEAD": Path(f"/wt/run-{issue}"))

    def _invoke(phase, issue, worktree, grounding, *, unattended=False, **kw):
        seen.append(unattended)
        return PhaseInvocation(phase, 0, "", "", "green", "https://x/pull/1")

    monkeypatch.setattr("kagura_engineer.run.invoke_phase", _invoke)
    report = run_idea(_cfg(), 7, unattended=True, memory=_FakeMemory(), repo_root=Path("/repo"))
    assert report.status is RunStatus.OK
    assert seen == [True, True]  # start + ship both threaded


def test_mcp_config_threads_to_invoke_phase(monkeypatch):
    seen = []
    monkeypatch.setattr("kagura_engineer.run.run_all",
                        lambda cfg: [CheckResult("gh-issue-driven", Status.OK, "x")])
    monkeypatch.setattr("kagura_engineer.run.ensure_worktree",
                        lambda root, issue, base="HEAD": Path(f"/wt/run-{issue}"))

    def _invoke(phase, issue, worktree, grounding, *, unattended=False, mcp_config=None, **kw):
        seen.append(mcp_config)
        return PhaseInvocation(phase, 0, "", "", "green", "https://x/pull/1")

    monkeypatch.setattr("kagura_engineer.run.invoke_phase", _invoke)
    cfg = _cfg().model_copy(update={"memory_mcp_config": "/tmp/m.json"})
    report = run_idea(cfg, 7, memory=_FakeMemory(), repo_root=Path("/repo"))
    assert report.status is RunStatus.OK
    assert seen == ["/tmp/m.json", "/tmp/m.json"]


def test_resume_skips_already_shipped_issue(monkeypatch):
    # a prior run marked this issue done → no-op (no worktree, no act phases)
    monkeypatch.setattr("kagura_engineer.run.run_all",
                        lambda cfg: [CheckResult("gh-issue-driven", Status.OK, "x")])

    def _boom(*a, **k):
        raise AssertionError("must not run worktree/act for an already-shipped issue")

    monkeypatch.setattr("kagura_engineer.run.ensure_worktree", _boom)
    monkeypatch.setattr("kagura_engineer.run.invoke_phase", _boom)
    mem = _FakeMemory()
    mem.state["run:7"] = {"done": True, "pr_url": "https://x/pull/7"}
    report = run_idea(_cfg(), 7, memory=mem, repo_root=Path("/repo"))
    assert report.status is RunStatus.OK
    assert report.pr_url == "https://x/pull/7"
    assert mem.remembered == []  # no re-persist


def test_successful_run_reinforces_recalled_memories(monkeypatch):
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 0, "", "", "green", None),
        "ship": PhaseInvocation("ship", 0, "", "", "green", "https://x/pull/1"),
    })
    mem = _FakeMemory()
    report = run_idea(_cfg(), 9, memory=mem, repo_root=Path("/repo"))
    assert report.status is RunStatus.OK
    assert mem.feedback_calls == ["m1"]  # the recalled memory id was reinforced


def test_no_remember_skips_reinforcement(monkeypatch):
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 0, "", "", "green", None),
        "ship": PhaseInvocation("ship", 0, "", "", "green", "https://x/pull/1"),
    })
    mem = _FakeMemory()
    run_idea(_cfg(), 9, no_remember=True, memory=mem, repo_root=Path("/repo"))
    assert mem.feedback_calls == []


def test_feedback_failure_does_not_lose_savepoint(monkeypatch):
    # a feedback hiccup must NOT skip remember/set_state(done) — cheap-resume
    # depends on the done-state being written.
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 0, "", "", "green", None),
        "ship": PhaseInvocation("ship", 0, "", "", "green", "https://x/pull/9"),
    })

    class _BrokenFeedback(_FakeMemory):
        def feedback(self, context_id, memory_id, *, weight=1.0):
            raise RuntimeError("feedback endpoint down")

    mem = _BrokenFeedback()
    report = run_idea(_cfg(), 42, memory=mem, repo_root=Path("/repo"))
    assert report.status is RunStatus.OK
    assert any(t == "savepoint" for t, _ in mem.remembered)   # savepoint written
    assert mem.state.get("run:42") == {"done": True, "pr_url": "https://x/pull/9"}  # done-state set
