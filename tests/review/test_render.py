import json

from kagura_engineer.review.render import print_table, to_json
from kagura_engineer.review.result import Finding, ReviewReport, ReviewStatus


def _report():
    return ReviewReport(
        target="feat/x", base="main", verdict="red", status=ReviewStatus.BLOCKED,
        summary={"total": 1, "blocking": 1},
        findings=[Finding("security", "HIGH", "a.py", 3, "SQLi")],
        detail="blocking verdict (red): 1 finding(s)",
        resume_hint="address the findings",
    )


def test_to_json_roundtrips():
    data = json.loads(to_json(_report()))
    assert data["verdict"] == "red"
    assert data["status"] == "blocked"
    assert data["findings"][0]["file"] == "a.py"
    assert data["findings"][0]["severity"] == "HIGH"
    assert data["summary"]["blocking"] == 1


def test_print_table_runs(capsys):
    print_table(_report())
    out = capsys.readouterr().out
    assert "SQLi" in out
    assert "red" in out


def _loop_report(status, n_iters, fixes):
    from kagura_engineer.review.result import ReviewLoopReport
    iters = [
        ReviewReport(target="HEAD", base="main", verdict="red", status=ReviewStatus.BLOCKED,
                     findings=[Finding("security", "HIGH", "a.py", 3, "SQLi")])
        for _ in range(n_iters - 1)
    ] + [_report() if status is ReviewStatus.BLOCKED else
         ReviewReport(target="HEAD", base="main", verdict="green", status=ReviewStatus.OK)]
    return ReviewLoopReport(target="HEAD", base="main", iterations=iters,
                            fixes_attempted=fixes, status=status, detail="d")


def test_loop_to_json_includes_iterations():
    from kagura_engineer.review.render import loop_to_json
    data = json.loads(loop_to_json(_loop_report(ReviewStatus.OK, 2, 1)))
    assert data["status"] == "ok"
    assert data["fixes_attempted"] == 1
    assert len(data["iterations"]) == 2
    assert data["iterations"][0]["verdict"] == "red"
    assert data["iterations"][-1]["verdict"] == "green"


def test_print_loop_table_runs(capsys):
    from kagura_engineer.review.render import print_loop_table
    print_loop_table(_loop_report(ReviewStatus.OK, 2, 1))
    out = capsys.readouterr().out
    assert "fix(es)" in out
    assert "iterations:" in out  # multi-iteration trail shown


def test_to_json_carries_profile():
    # issue #70: the review report serialises its ExecutionProfile.
    from dataclasses import replace

    from kagura_engineer.profile import ExecutionProfile
    from tests._constants import EXECUTION_PROFILE_KWARGS

    report = replace(_report(), profile=ExecutionProfile(**EXECUTION_PROFILE_KWARGS))
    data = json.loads(to_json(report))
    assert data["profile"]["reviewer_model"] == "qwen3-coder:480b"


def test_loop_to_json_carries_profile():
    from dataclasses import replace

    from kagura_engineer.profile import ExecutionProfile
    from kagura_engineer.review.render import loop_to_json
    from kagura_engineer.review.result import ReviewLoopReport
    from tests._constants import EXECUTION_PROFILE_KWARGS

    report = replace(
        ReviewLoopReport(target="HEAD", base="main"),
        profile=ExecutionProfile(**EXECUTION_PROFILE_KWARGS),
    )
    data = json.loads(loop_to_json(report))
    assert data["profile"]["brain_backend"] == "claude"


def test_to_json_profile_defaults_to_none():
    data = json.loads(to_json(_report()))
    assert data["profile"] is None
