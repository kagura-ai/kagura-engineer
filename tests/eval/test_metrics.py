"""Issue #57: objective-signal extraction from a run (+ optional review)."""
from kagura_engineer.eval.metrics import build_arm_run, outcome_verdict, pr_reached
from kagura_engineer.run.result import PhaseResult, RunReport, RunStatus
from kagura_engineer.review.result import (
    Finding, ReviewLoopReport, ReviewReport, ReviewStatus,
)


def _ok_report(issue=1, pr="https://x/pull/1", verdicts=("green", "green", "green")):
    phases = [
        PhaseResult("guard", RunStatus.OK, "ok"),
        PhaseResult("recall", RunStatus.OK, "5 memories"),
        PhaseResult("worktree", RunStatus.OK, "wt"),
    ]
    for name, v in zip(("start", "implement", "ship"), verdicts):
        phases.append(PhaseResult(name, RunStatus.OK, f"{name} ok", verdict=v))
    return RunReport(issue=issue, phases=phases, pr_url=pr)


def _blocked_report(issue=1, verdict="red"):
    return RunReport(issue=issue, phases=[
        PhaseResult("guard", RunStatus.OK, "ok"),
        PhaseResult("recall", RunStatus.OK, "5 memories"),
        PhaseResult("worktree", RunStatus.OK, "wt"),
        PhaseResult("start", RunStatus.BLOCKED, f"gate halt ({verdict})", verdict=verdict),
    ])


def _failed_report(issue=1):
    return RunReport(issue=issue, phases=[
        PhaseResult("guard", RunStatus.OK, "ok"),
        PhaseResult("recall", RunStatus.OK, "5 memories"),
        PhaseResult("worktree", RunStatus.OK, "wt"),
        PhaseResult("start", RunStatus.FAIL, "claude exited 1: boom"),
    ])


# --- outcome_verdict --------------------------------------------------------

def test_outcome_green_when_all_gates_green_and_pr_reached():
    assert outcome_verdict(_ok_report()) == "green"


def test_outcome_yellow_when_any_gate_yellow():
    assert outcome_verdict(_ok_report(verdicts=("green", "yellow", "green"))) == "yellow"


def test_outcome_is_halting_verdict_when_blocked():
    assert outcome_verdict(_blocked_report(verdict="red")) == "red"
    assert outcome_verdict(_blocked_report(verdict="unknown")) == "unknown"


def test_outcome_blocked_without_final_verdict_maps_to_unknown():
    # A guard-block (the final phase carries no verdict) must stay inside the
    # outcome vocabulary — never the literal "blocked", which ArmStats.from_runs
    # does not count (it would silently skew aggregates).
    guard_blocked = RunReport(issue=1, phases=[
        PhaseResult("guard", RunStatus.BLOCKED, "blocking checks failed"),
    ])
    assert guard_blocked.status is RunStatus.BLOCKED  # derived: worst phase
    assert outcome_verdict(guard_blocked) == "unknown"


def test_outcome_fail_when_run_failed():
    assert outcome_verdict(_failed_report()) == "fail"


# --- pr_reached -------------------------------------------------------------

def test_pr_reached_true_for_ok_with_url():
    assert pr_reached(_ok_report()) is True


def test_pr_reached_false_when_blocked():
    assert pr_reached(_blocked_report()) is False


def test_pr_reached_false_when_no_url():
    assert pr_reached(_ok_report(pr=None)) is False


# --- build_arm_run ----------------------------------------------------------

def test_build_arm_run_without_review_leaves_review_signals_none():
    arm = build_arm_run(1, grounded=True, run_report=_ok_report())
    assert arm.issue == 1
    assert arm.grounded is True
    assert arm.status is RunStatus.OK
    assert arm.pr_reached is True
    assert arm.outcome == "green"
    assert arm.findings_total is None
    assert arm.findings_blocking is None
    assert arm.fix_iterations is None


def _review_loop(findings, *, summary=None, fixes=0):
    final = ReviewReport(
        target="HEAD", base="main", verdict="green",
        status=ReviewStatus.OK, summary=summary or {}, findings=findings,
    )
    return ReviewLoopReport(
        target="HEAD", base="main", iterations=[final],
        fixes_attempted=fixes, status=ReviewStatus.OK,
    )


def test_build_arm_run_reads_review_summary_counts():
    review = _review_loop(
        findings=[Finding("bug", "HIGH", "a.py", 1, "x")],
        summary={"total": 4, "blocking": 1}, fixes=2,
    )
    arm = build_arm_run(2, grounded=False, run_report=_ok_report(issue=2), review_report=review)
    assert arm.findings_total == 4          # from summary, authoritative
    assert arm.findings_blocking == 1
    assert arm.fix_iterations == 2


def test_build_arm_run_derives_counts_from_findings_when_summary_absent():
    review = _review_loop(findings=[
        Finding("bug", "HIGH", "a.py", 1, "x"),
        Finding("style", "LOW", "b.py", 2, "y"),
        Finding("bug", "CRITICAL", "c.py", 3, "z"),
    ], fixes=1)
    arm = build_arm_run(3, grounded=True, run_report=_ok_report(issue=3), review_report=review)
    assert arm.findings_total == 3                 # len(findings)
    assert arm.findings_blocking == 2              # HIGH + CRITICAL
    assert arm.fix_iterations == 1
