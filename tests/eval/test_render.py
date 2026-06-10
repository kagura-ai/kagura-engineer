"""Issue #57: A/B eval renderers (JSON + rich table)."""
import json

from kagura_engineer.eval.render import to_json, print_table
from kagura_engineer.eval.result import ArmRun, EvalReport
from kagura_engineer.run.result import RunStatus


def _arm(issue, grounded, outcome, *, pr=True, ft=None, fb=None, fix=None):
    status = RunStatus.OK if outcome in ("green", "yellow") else (
        RunStatus.FAIL if outcome == "fail" else RunStatus.BLOCKED)
    return ArmRun(issue=issue, grounded=grounded, status=status, pr_reached=pr,
                  outcome=outcome, findings_total=ft, findings_blocking=fb, fix_iterations=fix)


def _report():
    return EvalReport(
        issues=[1, 2],
        grounded_runs=[_arm(1, True, "green", ft=1, fb=0, fix=0),
                       _arm(2, True, "green", ft=2, fb=1, fix=1)],
        control_runs=[_arm(1, False, "green", ft=3, fb=1, fix=1),
                      _arm(2, False, "red", pr=False, ft=6, fb=3, fix=3)],
        duration_s=12.5,
    )


def test_to_json_is_valid_and_carries_both_arms_and_uplift():
    data = json.loads(to_json(_report()))
    assert data["issues"] == [1, 2]
    assert data["grounded"]["n"] == 2
    assert data["control"]["n"] == 2
    assert data["grounded"]["pr_reached"] == 2
    assert data["control"]["pr_reached"] == 1
    # uplift block present with the verdict
    assert data["uplift"]["verdict"] == "improved"
    assert "pr_rate_delta" in data["uplift"]
    # per-issue rows for traceability
    assert len(data["per_issue"]) == 2


def test_to_json_handles_run_only_eval_with_null_review_means():
    rep = EvalReport(
        issues=[1],
        grounded_runs=[_arm(1, True, "green")],
        control_runs=[_arm(1, False, "green")],
    )
    data = json.loads(to_json(rep))
    assert data["grounded"]["mean_findings"] is None
    assert data["uplift"]["mean_findings_delta"] is None


def test_print_table_runs_without_error(capsys):
    print_table(_report())
    out = capsys.readouterr().out
    assert "grounded" in out.lower()
    assert "control" in out.lower()
    assert "improved" in out.lower()   # the headline verdict is shown
