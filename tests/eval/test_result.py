"""Issue #57: A/B aggregate stats + uplift verdict."""
from kagura_engineer.eval.result import ArmRun, ArmStats, EvalReport
from kagura_engineer.run.result import RunStatus


def _arm(issue, grounded, outcome, *, pr=True, ft=None, fb=None, fix=None):
    status = {
        "green": RunStatus.OK, "yellow": RunStatus.OK,
        "red": RunStatus.BLOCKED, "unknown": RunStatus.BLOCKED,
        "fail": RunStatus.FAIL,
    }[outcome]
    return ArmRun(
        issue=issue, grounded=grounded, status=status, pr_reached=pr,
        outcome=outcome, findings_total=ft, findings_blocking=fb, fix_iterations=fix,
    )


# --- ArmStats aggregation ---------------------------------------------------

def test_arm_stats_counts_outcomes():
    runs = [
        _arm(1, True, "green"),
        _arm(2, True, "yellow"),
        _arm(3, True, "red", pr=False),
        _arm(4, True, "fail", pr=False),
    ]
    stats = ArmStats.from_runs(runs, grounded=True)
    assert stats.n == 4
    assert stats.green == 1
    assert stats.yellow == 1
    assert stats.red == 1
    assert stats.failed == 1
    assert stats.pr_reached == 2
    assert stats.pr_rate == 0.5
    assert stats.green_rate == 0.25


def test_arm_stats_means_over_reviewed_runs_only():
    runs = [
        _arm(1, True, "green", ft=2, fb=0, fix=1),
        _arm(2, True, "green", ft=4, fb=2, fix=3),
        _arm(3, True, "green"),  # no review → excluded from means
    ]
    stats = ArmStats.from_runs(runs, grounded=True)
    assert stats.reviewed == 2
    assert stats.mean_findings == 3.0          # (2+4)/2
    assert stats.mean_blocking == 1.0          # (0+2)/2
    assert stats.mean_fix_iterations == 2.0    # (1+3)/2


def test_arm_stats_means_none_when_no_review():
    stats = ArmStats.from_runs([_arm(1, True, "green")], grounded=True)
    assert stats.reviewed == 0
    assert stats.mean_findings is None
    assert stats.mean_blocking is None
    assert stats.mean_fix_iterations is None


# --- EvalReport uplift ------------------------------------------------------

def _report(grounded_runs, control_runs):
    return EvalReport(
        issues=[r.issue for r in grounded_runs],
        grounded_runs=grounded_runs, control_runs=control_runs,
    )


def test_uplift_improved_when_grounded_ships_more_and_cleaner():
    grounded = [_arm(1, True, "green", ft=1, fb=0, fix=0),
                _arm(2, True, "green", ft=1, fb=0, fix=0)]
    control = [_arm(1, False, "green", ft=4, fb=2, fix=2, pr=True),
               _arm(2, False, "red", ft=6, fb=3, fix=3, pr=False)]
    up = _report(grounded, control).uplift
    assert up.pr_rate_delta > 0           # grounded shipped both, control shipped 1
    assert up.mean_findings_delta < 0     # grounded had fewer findings
    assert up.verdict == "improved"


def test_uplift_regressed_when_grounded_worse():
    grounded = [_arm(1, True, "red", pr=False, ft=5, fb=3, fix=3)]
    control = [_arm(1, False, "green", pr=True, ft=1, fb=0, fix=0)]
    up = _report(grounded, control).uplift
    assert up.verdict == "regressed"


def test_uplift_neutral_when_arms_identical():
    grounded = [_arm(1, True, "green", ft=2, fb=1, fix=1)]
    control = [_arm(1, False, "green", ft=2, fb=1, fix=1)]
    up = _report(grounded, control).uplift
    assert up.pr_rate_delta == 0
    assert up.verdict == "neutral"


def test_uplift_inconclusive_when_no_issues():
    up = EvalReport(issues=[], grounded_runs=[], control_runs=[]).uplift
    assert up.verdict == "inconclusive"
