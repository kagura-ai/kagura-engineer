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
        self.explore_result = []

    def load_pinned(self, context_id): return ["guardrail: TDD"]
    def recall(self, context_id, query, *, k=5): return ["decision A"]
    def recall_detailed(self, context_id, query, *, k=5): return [("m1", "decision A")]
    def explore(self, context_id, memory_id, *, depth=1): return self.explore_result
    def feedback(self, context_id, memory_id, *, weight=1.0):
        self.feedback_calls.append(memory_id)
    def remember(self, context_id, *, summary, content, type, tags=None):
        self.remembered.append((type, summary))
        return "mem-1"
    def get_state(self, context_id, key): return self.state.get(key)
    def set_state(self, context_id, key, value): self.state[key] = value


def _patch_boundaries(monkeypatch, *, blocking=False, phases=None, worktree=None):
    """Patch guard/worktree/workflow. `phases` maps phase->PhaseInvocation.

    `worktree`, if given, is the path `ensure_worktree` returns — pass a real
    tmp_path when the test inspects files the run writes there (else a fake path).
    """
    checks = [CheckResult("gh-issue-driven", Status.FAIL if blocking else Status.OK, "x")]
    monkeypatch.setattr("kagura_engineer.run.run_all", lambda cfg: checks)
    monkeypatch.setattr("kagura_engineer.run.ensure_worktree", lambda root, issue, base="HEAD", label=None: worktree or Path(f"/wt/run-{issue}"))
    phases = phases or {}

    def _invoke(phase, issue, worktree, grounding, **kw):
        # Unspecified phases default to green so a test only declares the phases
        # it cares about (e.g. a start-red test need not spell out implement/ship).
        return phases.get(phase) or PhaseInvocation(phase, 0, "", "", "green", None)

    monkeypatch.setattr("kagura_engineer.run.invoke_phase", _invoke)
    # issue #64: the ship no-PR-URL guard cross-checks GitHub via lookup_pr_url;
    # default it to "no PR found" so no test ever shells out to real `gh`
    # (raising=False keeps this patch inert until the seam exists).
    monkeypatch.setattr(
        "kagura_engineer.run.lookup_pr_url", lambda wt: None, raising=False,
    )
    # issue #80: after lookup finds no PR, the guard tries to push+open one. Default
    # it to "couldn't recover" so a #18 test stays a FAIL and never shells out to
    # real git/gh; the recovery test overrides this to return a URL.
    monkeypatch.setattr(
        "kagura_engineer.run.recover_open_pr", lambda wt, issue: None, raising=False,
    )


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
    assert [p.name for p in report.phases] == ["guard", "recall", "worktree", "start", "implement", "ship", "persist"]
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
    assert "boom" in report.phases[-1].detail  # stderr surfaced
    assert "ANTHROPIC_API_KEY" not in (report.resume_hint or "")  # non-auth error


# --- issue #19: surface headless claude auth failure + actionable hint -------


def test_phase_fail_surfaces_stdout_when_stderr_empty(monkeypatch):
    # claude prints some fatal errors (e.g. "Invalid API key") to stdout, NOT
    # stderr — the FAIL detail must never be an opaque "claude exited 1:" with an
    # empty tail (issue #19).
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation(
            "start", 1, "Invalid API key · Fix external API key", "", None, None),
    })
    report = run_idea(_cfg(), 42, memory=_FakeMemory(), repo_root=Path("/repo"))
    assert report.status is RunStatus.FAIL
    assert report.phases[-1].name == "start"
    assert "invalid api key" in report.phases[-1].detail.lower()  # stdout surfaced
    # actionable remedy for the stale-key trap, with the concrete issue number
    assert "ANTHROPIC_API_KEY" in (report.resume_hint or "")
    assert "run 42" in (report.resume_hint or "")


def test_phase_fail_auth_hint_only_on_auth_signature(monkeypatch):
    # A non-auth failure on stdout gets surfaced but NOT the ANTHROPIC_API_KEY hint.
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 1, "Traceback: boom", "", None, None),
    })
    report = run_idea(_cfg(), 42, memory=_FakeMemory(), repo_root=Path("/repo"))
    assert report.status is RunStatus.FAIL
    assert "boom" in report.phases[-1].detail
    assert "ANTHROPIC_API_KEY" not in (report.resume_hint or "")


def test_phase_fail_generic_api_key_text_does_not_trigger_auth_hint(monkeypatch):
    # A standalone "invalid api key" mention (e.g. unrelated model output about
    # API-key handling) must NOT trip the auth hint — only claude's full signature
    # ("Invalid API key · Fix external API key") should. The error is still
    # surfaced; just no ANTHROPIC_API_KEY remedy is attached.
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation(
            "start", 1, "note: the tool returned an invalid api key error", "",
            None, None),
    })
    report = run_idea(_cfg(), 42, memory=_FakeMemory(), repo_root=Path("/repo"))
    assert report.status is RunStatus.FAIL
    assert "invalid api key" in report.phases[-1].detail.lower()  # still surfaced
    assert "ANTHROPIC_API_KEY" not in (report.resume_hint or "")  # no false hint


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

    def _boom(root, issue, base="HEAD", label=None):
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
                        lambda root, issue, base="HEAD", label=None: Path(f"/wt/run-{issue}"))

    def _invoke(phase, issue, worktree, grounding, *, unattended=False, **kw):
        seen.append(unattended)
        return PhaseInvocation(phase, 0, "", "", "green", "https://x/pull/1")

    monkeypatch.setattr("kagura_engineer.run.invoke_phase", _invoke)
    report = run_idea(_cfg(), 7, unattended=True, memory=_FakeMemory(), repo_root=Path("/repo"))
    assert report.status is RunStatus.OK
    assert seen == [True, True, True]  # start + implement + ship all threaded


def test_mcp_config_threads_to_invoke_phase(monkeypatch):
    seen = []
    monkeypatch.setattr("kagura_engineer.run.run_all",
                        lambda cfg: [CheckResult("gh-issue-driven", Status.OK, "x")])
    monkeypatch.setattr("kagura_engineer.run.ensure_worktree",
                        lambda root, issue, base="HEAD", label=None: Path(f"/wt/run-{issue}"))

    def _invoke(phase, issue, worktree, grounding, *, unattended=False, mcp_config=None, **kw):
        seen.append(mcp_config)
        return PhaseInvocation(phase, 0, "", "", "green", "https://x/pull/1")

    monkeypatch.setattr("kagura_engineer.run.invoke_phase", _invoke)
    cfg = _cfg().model_copy(update={"memory_mcp_config": "/tmp/m.json"})
    report = run_idea(cfg, 7, memory=_FakeMemory(), repo_root=Path("/repo"))
    assert report.status is RunStatus.OK
    assert seen == ["/tmp/m.json", "/tmp/m.json", "/tmp/m.json"]


def test_run_label_isolates_worktree_branch_and_resume_key(monkeypatch):
    # issue #57: run_label must isolate the arm across all three issue-keyed
    # surfaces — worktree (label), start branch (--branch override), and the
    # resume-state key — so the control arm cannot reuse the grounded arm's state.
    seen_labels = []
    seen_branch = {}
    monkeypatch.setattr("kagura_engineer.run.run_all",
                        lambda cfg: [CheckResult("gh-issue-driven", Status.OK, "x")])

    def _ew(root, issue, base="HEAD", label=None):
        seen_labels.append(label)
        return Path(f"/wt/run-{issue}-{label}")
    monkeypatch.setattr("kagura_engineer.run.ensure_worktree", _ew)

    def _invoke(phase, issue, worktree, grounding, *, branch_override=None, **kw):
        seen_branch[phase] = branch_override
        return PhaseInvocation(phase, 0, "", "", "green", "https://x/pull/1")
    monkeypatch.setattr("kagura_engineer.run.invoke_phase", _invoke)

    mem = _FakeMemory()
    report = run_idea(_cfg(), 7, run_label="control", memory=mem, repo_root=Path("/repo"))
    assert report.status is RunStatus.OK
    assert seen_labels == ["control"]                  # worktree isolated per arm
    assert seen_branch["start"] == "run-7-control"     # start pinned to the arm branch
    assert seen_branch["implement"] is None            # build/ship follow current branch
    assert seen_branch["ship"] is None
    assert "run:7:control" in mem.state                # resume key suffixed by arm
    assert "run:7" not in mem.state                    # never the bare issue key


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


def test_grounding_enriched_with_explore_neighbors(monkeypatch):
    captured = {}
    monkeypatch.setattr("kagura_engineer.run.run_all",
                        lambda cfg: [CheckResult("gh-issue-driven", Status.OK, "x")])
    monkeypatch.setattr("kagura_engineer.run.ensure_worktree",
                        lambda root, issue, base="HEAD", label=None: Path(f"/wt/run-{issue}"))

    def _invoke(phase, issue, worktree, grounding, **kw):
        captured["grounding"] = list(grounding)
        return PhaseInvocation(phase, 0, "", "", "green", "https://x/pull/1")

    monkeypatch.setattr("kagura_engineer.run.invoke_phase", _invoke)
    mem = _FakeMemory()
    mem.explore_result = [("n1", "RELATED neighbor")]
    report = run_idea(_cfg(), 9, memory=mem, repo_root=Path("/repo"))
    assert report.status is RunStatus.OK
    g = captured["grounding"]
    assert "guardrail: TDD" in g and "decision A" in g  # pinned + recall
    assert "RELATED neighbor" in g                       # explore enrichment


# --- issue #57: `ground` toggle — the A/B control-arm switch ----------------


class _CountingMemory(_FakeMemory):
    """Records which recall/grounding calls were made, so the control arm can
    assert NO grounding was pulled."""
    def __init__(self):
        super().__init__()
        self.load_pinned_calls = 0
        self.recall_calls = 0
        self.explore_calls = 0

    def load_pinned(self, context_id):
        self.load_pinned_calls += 1
        return super().load_pinned(context_id)

    def recall_detailed(self, context_id, query, *, k=5):
        self.recall_calls += 1
        return super().recall_detailed(context_id, query, k=k)

    def explore(self, context_id, memory_id, *, depth=1):
        self.explore_calls += 1
        return super().explore(context_id, memory_id, depth=depth)


def test_control_arm_injects_no_grounding(monkeypatch):
    # ground=False is the A/B control arm: the loop runs identically but NO
    # grounding is pulled or injected — load_pinned / recall / explore are never
    # called, and the phase prompt grounding is empty.
    captured = {}
    monkeypatch.setattr("kagura_engineer.run.run_all",
                        lambda cfg: [CheckResult("gh-issue-driven", Status.OK, "x")])
    monkeypatch.setattr("kagura_engineer.run.ensure_worktree",
                        lambda root, issue, base="HEAD", label=None: Path(f"/wt/run-{issue}"))

    def _invoke(phase, issue, worktree, grounding, **kw):
        captured.setdefault("grounding", list(grounding))
        return PhaseInvocation(phase, 0, "", "", "green", "https://x/pull/1")

    monkeypatch.setattr("kagura_engineer.run.invoke_phase", _invoke)
    mem = _CountingMemory()
    mem.explore_result = [("n1", "RELATED neighbor")]
    report = run_idea(_cfg(), 9, memory=mem, ground=False, repo_root=Path("/repo"))
    assert report.status is RunStatus.OK
    assert captured["grounding"] == []        # nothing injected into the prompt
    assert mem.load_pinned_calls == 0          # no pinned pulled
    assert mem.recall_calls == 0               # no recall
    assert mem.explore_calls == 0              # no graph enrichment


def test_control_arm_skips_reinforcement(monkeypatch):
    # With no grounding recalled, there is nothing to reinforce — feedback() must
    # not be called even on a successful run.
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 0, "", "", "green", None),
        "ship": PhaseInvocation("ship", 0, "", "", "green", "https://x/pull/9"),
    })
    mem = _FakeMemory()
    report = run_idea(_cfg(), 9, memory=mem, ground=False, repo_root=Path("/repo"))
    assert report.status is RunStatus.OK
    assert mem.feedback_calls == []            # control arm reinforces nothing


def test_control_arm_recall_phase_reports_grounding_off(monkeypatch):
    # The recall phase still runs (resume state is read), but its detail names the
    # disabled-grounding state so the report is honest about which arm ran.
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 0, "", "", "green", None),
        "ship": PhaseInvocation("ship", 0, "", "", "green", "https://x/pull/9"),
    })
    report = run_idea(_cfg(), 9, memory=_FakeMemory(), ground=False, repo_root=Path("/repo"))
    recall_phase = next(p for p in report.phases if p.name == "recall")
    assert "grounding off" in recall_phase.detail.lower()


def test_grounded_arm_is_default(monkeypatch):
    # The default (ground unset) is the grounded arm: grounding IS injected.
    captured = {}
    monkeypatch.setattr("kagura_engineer.run.run_all",
                        lambda cfg: [CheckResult("gh-issue-driven", Status.OK, "x")])
    monkeypatch.setattr("kagura_engineer.run.ensure_worktree",
                        lambda root, issue, base="HEAD", label=None: Path(f"/wt/run-{issue}"))

    def _invoke(phase, issue, worktree, grounding, **kw):
        captured.setdefault("grounding", list(grounding))
        return PhaseInvocation(phase, 0, "", "", "green", "https://x/pull/1")

    monkeypatch.setattr("kagura_engineer.run.invoke_phase", _invoke)
    report = run_idea(_cfg(), 9, memory=_FakeMemory(), repo_root=Path("/repo"))
    assert report.status is RunStatus.OK
    assert "guardrail: TDD" in captured["grounding"]  # pinned grounding injected


def test_control_arm_still_resumes_already_shipped(monkeypatch):
    # Resume is part of the loop, not grounding: a control-arm run of an
    # already-shipped issue still no-ops via the resume marker.
    monkeypatch.setattr("kagura_engineer.run.run_all",
                        lambda cfg: [CheckResult("gh-issue-driven", Status.OK, "x")])

    def _boom(*a, **k):
        raise AssertionError("must not run worktree/act for an already-shipped issue")

    monkeypatch.setattr("kagura_engineer.run.ensure_worktree", _boom)
    monkeypatch.setattr("kagura_engineer.run.invoke_phase", _boom)
    mem = _FakeMemory()
    mem.state["run:7"] = {"done": True, "pr_url": "https://x/pull/7"}
    report = run_idea(_cfg(), 7, memory=mem, ground=False, repo_root=Path("/repo"))
    assert report.status is RunStatus.OK
    assert report.pr_url == "https://x/pull/7"


def test_explore_failure_does_not_fail_recall(monkeypatch):
    monkeypatch.setattr("kagura_engineer.run.run_all",
                        lambda cfg: [CheckResult("gh-issue-driven", Status.OK, "x")])
    monkeypatch.setattr("kagura_engineer.run.ensure_worktree",
                        lambda root, issue, base="HEAD", label=None: Path(f"/wt/run-{issue}"))
    monkeypatch.setattr("kagura_engineer.run.invoke_phase",
                        lambda phase, issue, wt, g, **kw: PhaseInvocation(phase, 0, "", "", "green", "https://x/pull/1"))

    class _ExplodeExplore(_FakeMemory):
        def explore(self, context_id, memory_id, *, depth=1):
            raise RuntimeError("explore down")

    report = run_idea(_cfg(), 9, memory=_ExplodeExplore(), repo_root=Path("/repo"))
    assert report.status is RunStatus.OK  # explore failure is non-fatal
    recall_phase = next(p for p in report.phases if p.name == "recall")
    assert recall_phase.status is RunStatus.OK


# --- issue #9: dedicated implement phase between start and ship ------------


def test_phase_sequence_includes_implement(monkeypatch):
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 0, "", "", "green", None),
        "implement": PhaseInvocation("implement", 0, "", "", "green", None),
        "ship": PhaseInvocation("ship", 0, "", "", "green", "https://x/pull/9"),
    })
    # head moves between start and ship → a commit was produced.
    shas = iter(["before", "after"])
    monkeypatch.setattr("kagura_engineer.run.head_rev", lambda wt: next(shas))
    report = run_idea(_cfg(), 42, memory=_FakeMemory(), repo_root=Path("/repo"))
    assert report.status is RunStatus.OK
    assert [p.name for p in report.phases] == \
        ["guard", "recall", "worktree", "start", "implement", "ship", "persist"]


def test_review_profile_recorded_once_implement_runs(monkeypatch):
    # issue #74: run/goal delegate code review to the brain's in-phase
    # /code-review, so once the implement phase runs the report records the
    # reviewer (the resolved brain backend — "claude" with the test config).
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 0, "", "", "green", None),
        "implement": PhaseInvocation("implement", 0, "", "", "green", None),
        "ship": PhaseInvocation("ship", 0, "", "", "green", "https://x/pull/9"),
    })
    shas = iter(["before", "after"])
    monkeypatch.setattr("kagura_engineer.run.head_rev", lambda wt: next(shas))
    report = run_idea(_cfg(), 42, memory=_FakeMemory(), repo_root=Path("/repo"))
    assert report.review is not None
    assert report.review.provider == "claude"
    assert report.review.via == "brain in-phase /code-review"


def test_review_profile_none_when_halted_before_implement(monkeypatch):
    # issue #74 AC3: a run halted at the design gate (start) never reached the
    # code-review phase — its review record is null, not a fabricated reviewer.
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 0, "", "", "red", None),
    })
    report = run_idea(_cfg(), 42, memory=_FakeMemory(), repo_root=Path("/repo"))
    assert report.status is RunStatus.BLOCKED
    assert report.review is None


def test_review_profile_none_when_guard_blocks(monkeypatch):
    # A run blocked at guard never resolved a brain → no review record.
    _patch_boundaries(monkeypatch, blocking=True)
    report = run_idea(_cfg(), 42, memory=_FakeMemory(), repo_root=Path("/repo"))
    assert report.review is None


def test_review_profile_none_when_implement_fails_to_launch(monkeypatch):
    # issue #74 /code-review finding: an implement phase that never launched
    # (e.g. claude binary missing → OSError) reviewed nothing — the record must
    # stay null, not a fabricated reviewer read off the brain call.
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 0, "", "", "green", None),
    })

    def _invoke(phase, issue, worktree, grounding, **kw):
        if phase == "implement":
            raise OSError("claude binary missing")
        return PhaseInvocation(phase, 0, "", "", "green", None)

    monkeypatch.setattr("kagura_engineer.run.invoke_phase", _invoke)
    monkeypatch.setattr("kagura_engineer.run.head_rev", lambda wt: None)
    report = run_idea(_cfg(), 42, memory=_FakeMemory(), repo_root=Path("/repo"))
    assert report.status is RunStatus.FAIL
    assert report.review is None


def test_review_profile_none_when_implement_exits_nonzero(monkeypatch):
    # issue #74 /code-review finding: an implement phase that exited non-zero
    # cannot be assumed to have run its in-phase /code-review — fail safe: null.
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 0, "", "", "green", None),
        "implement": PhaseInvocation("implement", 1, "", "boom", None, None),
    })
    monkeypatch.setattr("kagura_engineer.run.head_rev", lambda wt: None)
    report = run_idea(_cfg(), 42, memory=_FakeMemory(), repo_root=Path("/repo"))
    assert report.status is RunStatus.FAIL
    assert report.review is None


def test_review_profile_none_when_code_review_never(monkeypatch):
    # issue #75: review.code_review="never" forbids the in-phase /code-review,
    # so the #74 record must stay null ("review: none ran") — recording a
    # reviewer for a review the policy forbade would be a fabrication.
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 0, "", "", "green", None),
        "implement": PhaseInvocation("implement", 0, "", "", "green", None),
        "ship": PhaseInvocation("ship", 0, "", "", "green", "https://x/pull/9"),
    })
    shas = iter(["before", "after"])
    monkeypatch.setattr("kagura_engineer.run.head_rev", lambda wt: next(shas))
    cfg = _cfg().model_copy(update={
        "review": _cfg().review.model_copy(update={"code_review": "never"}),
    })
    report = run_idea(cfg, 42, memory=_FakeMemory(), repo_root=Path("/repo"))
    assert report.status is RunStatus.OK
    assert report.review is None


def test_run_threads_review_policy_to_invoke_phase(monkeypatch):
    # issue #75: repo.yaml's review.code_review/effort must reach invoke_phase
    # (and thence the implement prompt) — config that never leaves run_idea
    # would be a silent no-op.
    _patch_boundaries(monkeypatch, phases={
        "ship": PhaseInvocation("ship", 0, "", "", "green", "https://x/pull/9"),
    })
    seen: list[dict] = []

    def _invoke(phase, issue, worktree, grounding, **kw):
        seen.append({"phase": phase, **kw})
        return PhaseInvocation(phase, 0, "", "", "green",
                               "https://x/pull/9" if phase == "ship" else None)

    monkeypatch.setattr("kagura_engineer.run.invoke_phase", _invoke)
    shas = iter(["before", "after"])
    monkeypatch.setattr("kagura_engineer.run.head_rev", lambda wt: next(shas))
    cfg = _cfg().model_copy(update={
        "review": _cfg().review.model_copy(
            update={"code_review": "always", "effort": "high"}),
    })
    run_idea(cfg, 42, memory=_FakeMemory(), repo_root=Path("/repo"))
    implement = next(k for k in seen if k["phase"] == "implement")
    assert implement["code_review"] == "always"
    assert implement["review_effort"] == "high"


def test_implement_no_commit_is_fail(monkeypatch):
    # implement ran green but produced NO commit → ship has nothing; fail clearly
    # at implement instead of the confusing "ship red" (issue #9).
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 0, "", "", "green", None),
        "implement": PhaseInvocation("implement", 0, "", "", "green", None),
        "ship": PhaseInvocation("ship", 0, "", "", "green", "https://x/pull/9"),
    })
    monkeypatch.setattr("kagura_engineer.run.head_rev", lambda wt: "unchanged-sha")
    report = run_idea(_cfg(), 42, memory=_FakeMemory(), repo_root=Path("/repo"))
    assert report.status is RunStatus.FAIL
    assert report.phases[-1].name == "implement"
    assert "no commit" in report.phases[-1].detail.lower()
    assert "ship" not in [p.name for p in report.phases]  # ship never ran


def test_implement_with_commit_proceeds_to_ship(monkeypatch):
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 0, "", "", "green", None),
        "implement": PhaseInvocation("implement", 0, "", "", "green", None),
        "ship": PhaseInvocation("ship", 0, "", "", "green", "https://x/pull/9"),
    })
    shas = iter(["before", "after"])  # HEAD changed → commit produced
    monkeypatch.setattr("kagura_engineer.run.head_rev", lambda wt: next(shas))
    report = run_idea(_cfg(), 42, memory=_FakeMemory(), repo_root=Path("/repo"))
    assert report.status is RunStatus.OK
    assert report.pr_url == "https://x/pull/9"


def test_implement_head_rev_unreadable_skips_check(monkeypatch):
    # If HEAD can't be read (head_rev → None), degrade to "skip the check" rather
    # than false-failing — best-effort, matching the per-phase isolation invariant.
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 0, "", "", "green", None),
        "implement": PhaseInvocation("implement", 0, "", "", "green", None),
        "ship": PhaseInvocation("ship", 0, "", "", "green", "https://x/pull/9"),
    })
    monkeypatch.setattr("kagura_engineer.run.head_rev", lambda wt: None)
    report = run_idea(_cfg(), 42, memory=_FakeMemory(), repo_root=Path("/repo"))
    assert report.status is RunStatus.OK  # check skipped, ship proceeds


# --- issue #18: a green ship with no PR URL is a false success ---------------


def test_ship_green_without_pr_url_is_fail(monkeypatch):
    # issue #18: ship self-reported green but produced NO PR URL — the branch was
    # never pushed / no PR opened. The run must NOT report OK / exit 0 "PR reached".
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 0, "", "", "green", None),
        "implement": PhaseInvocation("implement", 0, "", "", "green", None),
        "ship": PhaseInvocation("ship", 0, "", "", "green", None),  # green, no pr_url
    })
    shas = iter(["before", "after"])  # implement produced a commit
    monkeypatch.setattr("kagura_engineer.run.head_rev", lambda wt: next(shas))
    mem = _FakeMemory()
    report = run_idea(_cfg(), 42, memory=mem, repo_root=Path("/repo"))
    assert report.status is RunStatus.FAIL
    assert STATUS_EXIT[report.status] != 0  # never a success exit
    assert report.phases[-1].name == "ship"
    assert "pr" in report.phases[-1].detail.lower()
    assert report.pr_url is None
    assert report.resume_hint  # tells the operator how to recover
    assert not any(t == "savepoint" for t, _ in mem.remembered)  # persist never ran


def test_ship_green_without_pr_url_persists_child_stdout(monkeypatch, tmp_path):
    # issue #38: on the silent green-ship-no-PR FAIL, the child `claude -p`
    # stdout is the only trace of *why* push / PR was skipped — persist it to the
    # worktree's `.kagura/` dir and point the operator at it in the detail, so the
    # skip is diagnosable without a re-run.
    reasoning = "ship: I committed locally but never ran git push / gh pr create"
    _patch_boundaries(monkeypatch, worktree=tmp_path, phases={
        "ship": PhaseInvocation("ship", 0, reasoning, "", "green", None),
    })
    shas = iter(["before", "after"])  # implement produced a commit
    monkeypatch.setattr("kagura_engineer.run.head_rev", lambda wt: next(shas))

    report = run_idea(_cfg(), 42, memory=_FakeMemory(), repo_root=Path("/repo"))
    assert report.status is RunStatus.FAIL
    log = tmp_path / ".kagura" / "ship-stdout.log"
    assert log.exists()
    assert reasoning in log.read_text()
    # the operator is pointed at the saved log from the failure detail
    assert ".kagura" in report.phases[-1].detail


def test_ship_guard_checks_ships_own_url_not_an_earlier_phase(monkeypatch):
    # The guard must check ship's OWN artifact (inv.pr_url), not the accumulated
    # pr_url — a stray URL emitted by an earlier phase must not mask a ship that
    # produced none. (review follow-up for #18.)
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 0, "", "", "green", None),
        "implement": PhaseInvocation("implement", 0, "", "", "green", "https://x/pull/99"),
        "ship": PhaseInvocation("ship", 0, "", "", "green", None),  # ship itself: no URL
    })
    shas = iter(["before", "after"])  # implement produced a commit
    monkeypatch.setattr("kagura_engineer.run.head_rev", lambda wt: next(shas))
    report = run_idea(_cfg(), 42, memory=_FakeMemory(), repo_root=Path("/repo"))
    assert report.status is RunStatus.FAIL  # NOT masked by the earlier URL
    assert report.phases[-1].name == "ship"


# --- issue #64: a healthy PR must not be failed on a dropped URL marker -------


def test_ship_green_with_dropped_url_marker_recovers_pr_from_github(monkeypatch):
    # The dogfooded false-negative: ship pushed and opened a healthy PR (ready,
    # CI green) but the transcript closed with the reviewer's `## Verdict: green`
    # and dropped BOTH trailing markers → pr_url=None → the #18 guard failed the
    # run and `goal` halted mid-milestone. Before declaring that false success,
    # the guard must cross-check GitHub: PR found → status=ok with that URL.
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 0, "", "", "green", None),
        "implement": PhaseInvocation("implement", 0, "", "", "green", None),
        # ship verdict recovered via the native `## Verdict: green` fallback,
        # but no KAGURA_PR_URL marker was emitted.
        "ship": PhaseInvocation("ship", 0, "…\n## Verdict: green\n", "", "green", None),
    })
    shas = iter(["before", "after"])  # implement produced a commit
    monkeypatch.setattr("kagura_engineer.run.head_rev", lambda wt: next(shas))
    monkeypatch.setattr(
        "kagura_engineer.run.lookup_pr_url",
        lambda wt: "https://github.com/o/r/pull/19",
    )
    mem = _FakeMemory()
    report = run_idea(_cfg(), 42, memory=mem, repo_root=Path("/repo"))
    assert report.status is RunStatus.OK
    assert STATUS_EXIT[report.status] == 0
    assert report.pr_url == "https://github.com/o/r/pull/19"
    ship = next(p for p in report.phases if p.name == "ship")
    assert ship.status is RunStatus.OK
    assert "github" in ship.detail.lower()  # detail says the URL was recovered
    assert any(t == "savepoint" for t, _ in mem.remembered)  # persist ran
    assert mem.state.get("run:42", {}).get("done") is True   # resume marker set


def test_ship_green_no_url_marker_and_no_github_pr_stays_fail(monkeypatch):
    # The cross-check finding nothing preserves the #18 fail-secure guard: a
    # green ship with neither a URL marker nor an actual PR is a false success.
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 0, "", "", "green", None),
        "implement": PhaseInvocation("implement", 0, "", "", "green", None),
        "ship": PhaseInvocation("ship", 0, "", "", "green", None),
    })
    shas = iter(["before", "after"])
    monkeypatch.setattr("kagura_engineer.run.head_rev", lambda wt: next(shas))
    monkeypatch.setattr("kagura_engineer.run.lookup_pr_url", lambda wt: None)
    # issue #80: be explicit that the recovery also can't open one (rather than
    # leaning on the _patch_boundaries default) so the fail-secure invariant is
    # visible right here.
    monkeypatch.setattr("kagura_engineer.run.recover_open_pr", lambda wt, issue: None)
    report = run_idea(_cfg(), 42, memory=_FakeMemory(), repo_root=Path("/repo"))
    assert report.status is RunStatus.FAIL
    assert report.phases[-1].name == "ship"


# --- issue #80: a green ship that opened no PR is recovered by the orchestrator -


def test_ship_green_no_pr_recovered_by_orchestrator_push(monkeypatch):
    # issue #80: ship went green but stopped after the gate2 review without
    # pushing / opening a PR. With no PR to look up, the orchestrator finishes the
    # job (push + open) instead of halting a complete, gate-green run.
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 0, "", "", "green", None),
        "implement": PhaseInvocation("implement", 0, "", "", "green", None),
        "ship": PhaseInvocation("ship", 0, "", "", "green", None),  # green, no pr_url
    })
    shas = iter(["before", "after"])  # implement produced a commit
    monkeypatch.setattr("kagura_engineer.run.head_rev", lambda wt: next(shas))
    monkeypatch.setattr("kagura_engineer.run.lookup_pr_url", lambda wt: None)
    monkeypatch.setattr(
        "kagura_engineer.run.recover_open_pr",
        lambda wt, issue: "https://github.com/o/r/pull/80",
    )
    mem = _FakeMemory()
    report = run_idea(_cfg(), 42, memory=mem, repo_root=Path("/repo"))
    assert report.status is RunStatus.OK
    assert STATUS_EXIT[report.status] == 0
    assert report.pr_url == "https://github.com/o/r/pull/80"
    ship = next(p for p in report.phases if p.name == "ship")
    assert ship.status is RunStatus.OK
    assert "orchestrator" in ship.detail.lower()  # detail says we opened it
    assert any(t == "savepoint" for t, _ in mem.remembered)  # persist ran
    assert mem.state.get("run:42", {}).get("done") is True   # resume marker set


def test_phase_fail_with_no_output_is_not_opaque(monkeypatch):
    # issue #19 follow-up: when claude exits non-zero with BOTH streams empty, the
    # detail must say something rather than a bare "claude exited 1:".
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 1, "", "", None, None),
    })
    report = run_idea(_cfg(), 42, memory=_FakeMemory(), repo_root=Path("/repo"))
    assert report.status is RunStatus.FAIL
    assert "no output captured" in report.phases[-1].detail.lower()


# --- issue #14: close the memory client we own (cloud loop hangs otherwise) ---


class _ClosableFakeMemory(_FakeMemory):
    def __init__(self):
        super().__init__()
        self.closed = False

    def close(self):
        self.closed = True


def test_run_closes_owned_memory_client(monkeypatch):
    # When run_idea creates the client itself (memory=None) it must close it —
    # a cloud client's persistent event loop otherwise hangs the process at exit.
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 0, "", "", "green", None),
        "ship": PhaseInvocation("ship", 0, "", "", "green", "https://x/pull/9"),
    })
    owned = _ClosableFakeMemory()
    monkeypatch.setattr("kagura_engineer.run.resolve_memory_client", lambda cfg: owned)
    report = run_idea(_cfg(), 42, repo_root=Path("/repo"))  # no memory= → run owns it
    assert report.status is RunStatus.OK
    assert owned.closed is True


def test_run_does_not_close_injected_memory_client(monkeypatch):
    # An injected client is owned by the caller (goal / tests) — do NOT close it.
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 0, "", "", "green", None),
        "ship": PhaseInvocation("ship", 0, "", "", "green", "https://x/pull/9"),
    })
    injected = _ClosableFakeMemory()
    report = run_idea(_cfg(), 42, memory=injected, repo_root=Path("/repo"))
    assert report.status is RunStatus.OK
    assert injected.closed is False


def test_run_closes_owned_client_even_when_halted(monkeypatch):
    # close() runs on the failure/halt paths too (finally), not only on success.
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 0, "", "", "red", None),  # halt at start
    })
    owned = _ClosableFakeMemory()
    monkeypatch.setattr("kagura_engineer.run.resolve_memory_client", lambda cfg: owned)
    report = run_idea(_cfg(), 42, repo_root=Path("/repo"))
    assert report.status is RunStatus.BLOCKED
    assert owned.closed is True


def test_run_owned_client_without_close_is_safe(monkeypatch):
    # A client with no close() (e.g. LocalMemoryClient) must not crash the run.
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 0, "", "", "green", None),
        "ship": PhaseInvocation("ship", 0, "", "", "green", "https://x/pull/9"),
    })
    monkeypatch.setattr("kagura_engineer.run.resolve_memory_client", lambda cfg: _FakeMemory())
    report = run_idea(_cfg(), 42, repo_root=Path("/repo"))  # _FakeMemory has no close()
    assert report.status is RunStatus.OK


# --- issue #12: stream incremental phase progress to a sink ----------------


def test_progress_sink_emits_enter_and_exit_for_each_act_phase(monkeypatch):
    # A long run is opaque without incremental feedback: each act phase must
    # announce on enter (▶) BEFORE its multi-minute claude call, and on exit.
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 0, "", "", "green", None),
        "implement": PhaseInvocation("implement", 0, "", "", "green", None),
        "ship": PhaseInvocation("ship", 0, "", "", "green", "https://x/pull/9"),
    })
    shas = iter(["before", "after"])  # implement committed → proceeds to ship
    monkeypatch.setattr("kagura_engineer.run.head_rev", lambda wt: next(shas))
    lines: list[str] = []
    report = run_idea(_cfg(), 42, memory=_FakeMemory(), repo_root=Path("/repo"),
                      progress=lines.append)
    assert report.status is RunStatus.OK
    for phase in ("start", "implement", "ship"):
        assert any(l.startswith("▶") and phase in l for l in lines), f"no enter line for {phase}: {lines}"
    # exit lines carry the OK icon + the gate verdict
    assert any("✅" in l and "start" in l and "green" in l for l in lines), lines


def test_progress_enter_precedes_exit_per_phase(monkeypatch):
    # The enter marker must come before the exit marker for the same phase, so a
    # stalled phase shows "▶ running" and never a premature "done".
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 0, "", "", "green", None),
        "ship": PhaseInvocation("ship", 0, "", "", "green", "https://x/pull/9"),
    })
    lines: list[str] = []
    run_idea(_cfg(), 42, memory=_FakeMemory(), repo_root=Path("/repo"), progress=lines.append)
    enter = next(i for i, l in enumerate(lines) if l.startswith("▶") and "start" in l)
    exit_ = next(i for i, l in enumerate(lines) if "✅" in l and "start" in l)
    assert enter < exit_, lines


def test_progress_sink_emits_blocked_line_on_halt(monkeypatch):
    # A gate halt must surface as a blocked progress line, not silence.
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 0, "", "", "red", None),
    })
    lines: list[str] = []
    report = run_idea(_cfg(), 42, memory=_FakeMemory(), repo_root=Path("/repo"),
                      progress=lines.append)
    assert report.status is RunStatus.BLOCKED
    assert any("⏸" in l and "start" in l and "red" in l for l in lines), lines


# --- issue #70: grounding-evidence progress line -----------------------------


def test_grounding_evidence_line_streams_real_counts(monkeypatch):
    # The startup header proves intent; this line proves what actually
    # happened: the real pinned/recalled counts and the exact context id.
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 0, "", "", "green", None),
        "ship": PhaseInvocation("ship", 0, "", "", "green", "https://x/pull/9"),
    })
    lines: list[str] = []
    run_idea(_cfg(), 42, memory=_FakeMemory(), repo_root=Path("/repo"),
             progress=lines.append)
    # _FakeMemory grounds with 1 pinned + 1 recalled memory.
    assert (
        f"grounding: pinned 1 + recalled 1 from context {VALID_CONTEXT_UUID}"
        in lines
    ), lines


def test_grounding_evidence_disabled_form_in_control_arm(monkeypatch):
    # The eval control arm pulls no grounding — the evidence line must say so
    # honestly instead of claiming zero-count grounding happened.
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 0, "", "", "green", None),
        "ship": PhaseInvocation("ship", 0, "", "", "green", "https://x/pull/9"),
    })
    lines: list[str] = []
    run_idea(_cfg(), 42, ground=False, memory=_FakeMemory(),
             repo_root=Path("/repo"), progress=lines.append)
    assert "grounding: none (recall disabled)" in lines, lines
    assert not any(l.startswith("grounding: pinned") for l in lines)


def test_progress_sink_is_optional(monkeypatch):
    # Default (no sink) must not crash — the run still produces a normal report.
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 0, "", "", "green", None),
        "ship": PhaseInvocation("ship", 0, "", "", "green", "https://x/pull/9"),
    })
    report = run_idea(_cfg(), 42, memory=_FakeMemory(), repo_root=Path("/repo"))
    assert report.status is RunStatus.OK


# --- failover: drain the WAL at run start ----------------------------------

def test_run_drains_failover_client_at_start(monkeypatch):
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 0, "", "", "green", None),
        "implement": PhaseInvocation("implement", 0, "", "", "green", None),
        "ship": PhaseInvocation("ship", 0, "", "", "green", "https://x/pull/9"),
    })
    shas = iter(["before", "after"])
    monkeypatch.setattr("kagura_engineer.run.head_rev", lambda wt: next(shas))

    class _DrainMem(_FakeMemory):
        def __init__(self):
            super().__init__(); self.drained = 0
        def drain(self):
            self.drained += 1
            return 0

    mem = _DrainMem()
    report = run_idea(_cfg(), 42, memory=mem, repo_root=Path("/repo"))
    assert report.status is RunStatus.OK
    assert mem.drained == 1                              # drained exactly once at start


def test_run_drain_failure_does_not_fail_run(monkeypatch):
    _patch_boundaries(monkeypatch, phases={
        "start": PhaseInvocation("start", 0, "", "", "green", None),
        "implement": PhaseInvocation("implement", 0, "", "", "green", None),
        "ship": PhaseInvocation("ship", 0, "", "", "green", "https://x/pull/9"),
    })
    shas = iter(["before", "after"])
    monkeypatch.setattr("kagura_engineer.run.head_rev", lambda wt: next(shas))

    class _BoomDrain(_FakeMemory):
        def drain(self):
            raise RuntimeError("drain blew up")

    report = run_idea(_cfg(), 42, memory=_BoomDrain(), repo_root=Path("/repo"))
    assert report.status is RunStatus.OK                 # drain failure is non-fatal
