import json

from kagura_engineer.goal.render import print_table, to_json
from kagura_engineer.goal.result import GoalReport
from kagura_engineer.run.result import PhaseResult, RunReport, RunStatus


def _report():
    return GoalReport(
        milestone="v0.3",
        issues=[
            RunReport(issue=1, pr_url="https://x/pull/1"),
            RunReport(issue=2, phases=[PhaseResult("act", RunStatus.BLOCKED, "gate")]),
        ],
        status=RunStatus.BLOCKED,
        detail="halted at issue #2",
        resume_hint="resolve #2",
    )


def test_to_json_shape():
    data = json.loads(to_json(_report()))
    assert data["milestone"] == "v0.3"
    assert data["status"] == "blocked"
    assert data["completed"] == 1
    assert data["total"] == 2
    assert data["issues"][0] == {"issue": 1, "status": "ok", "pr_url": "https://x/pull/1"}


def test_print_table_runs(capsys):
    print_table(_report())
    out = capsys.readouterr().out
    assert "v0.3" in out
    assert "#2" in out
    assert "resolve #2" in out
