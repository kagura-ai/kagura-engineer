"""Issue #57: the A/B orchestrator runs each issue in both arms and aggregates."""
from kagura_engineer.eval import run_ab_eval
from kagura_engineer.run.result import PhaseResult, RunReport, RunStatus
from kagura_engineer.review.result import ReviewLoopReport, ReviewReport, ReviewStatus


def _report(issue, *, status=RunStatus.OK, pr="https://x/pull/1", verdict="green"):
    return RunReport(issue=issue, pr_url=pr, phases=[
        PhaseResult("ship", status, "ship", verdict=verdict),
    ])


def test_runs_both_arms_for_each_issue():
    calls = []

    def run_fn(issue, *, ground):
        calls.append((issue, ground))
        return _report(issue)

    report = run_ab_eval([7, 8], run_fn)
    # one grounded + one control call per issue, in order
    assert calls == [(7, True), (7, False), (8, True), (8, False)]
    assert report.issues == [7, 8]
    assert len(report.grounded_runs) == 2
    assert len(report.control_runs) == 2
    assert all(r.grounded for r in report.grounded_runs)
    assert not any(r.grounded for r in report.control_runs)


def test_grounded_arm_passes_ground_true_control_false():
    seen = {}

    def run_fn(issue, *, ground):
        seen[ground] = seen.get(ground, 0) + 1
        return _report(issue)

    run_ab_eval([1], run_fn)
    assert seen == {True: 1, False: 1}


def test_review_fn_signals_flow_into_arm_runs():
    def run_fn(issue, *, ground):
        return _report(issue)

    def review_fn(run_report, grounded):
        # grounded arm has fewer findings than control, in this fake
        n = 1 if grounded else 5
        final = ReviewReport(target="HEAD", base="main", verdict="green",
                             status=ReviewStatus.OK, summary={"total": n, "blocking": 0})
        return ReviewLoopReport(target="HEAD", base="main", iterations=[final],
                               fixes_attempted=0, status=ReviewStatus.OK)

    report = run_ab_eval([1], run_fn, review_fn=review_fn)
    assert report.grounded_runs[0].findings_total == 1
    assert report.control_runs[0].findings_total == 5


def test_progress_sink_emits_per_arm():
    def run_fn(issue, *, ground):
        return _report(issue)

    lines = []
    run_ab_eval([1], run_fn, progress=lines.append)
    # at least one line mentioning each arm
    assert any("grounded" in l.lower() for l in lines)
    assert any("control" in l.lower() for l in lines)


def test_no_review_fn_leaves_review_signals_none():
    def run_fn(issue, *, ground):
        return _report(issue)

    report = run_ab_eval([1], run_fn)
    assert report.grounded_runs[0].findings_total is None
