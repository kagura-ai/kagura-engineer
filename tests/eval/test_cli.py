"""Issue #57: `kagura-engineer eval` CLI wiring."""
import json

from typer.testing import CliRunner

from kagura_engineer.cli import app
from kagura_engineer.eval.result import ArmRun, EvalReport
from kagura_engineer.run.result import PhaseResult, RunReport, RunStatus

runner = CliRunner()


def _eval_report():
    return EvalReport(
        issues=[1, 2],
        grounded_runs=[ArmRun(1, True, RunStatus.OK, True, "green"),
                       ArmRun(2, True, RunStatus.OK, True, "green")],
        control_runs=[ArmRun(1, False, RunStatus.OK, True, "green"),
                      ArmRun(2, False, RunStatus.BLOCKED, False, "red")],
    )


def test_eval_runs_and_exits_0(write_cfg, monkeypatch):
    monkeypatch.setattr("kagura_engineer.cli.run_ab_eval",
                        lambda issues, run_fn, **kw: _eval_report())
    result = runner.invoke(app, ["eval", "1", "2", "--config", str(write_cfg)])
    assert result.exit_code == 0
    assert "uplift" in result.stdout.lower()


def test_eval_json_emits_report(write_cfg, monkeypatch):
    monkeypatch.setattr("kagura_engineer.cli.run_ab_eval",
                        lambda issues, run_fn, **kw: _eval_report())
    result = runner.invoke(app, ["eval", "1", "2", "--config", str(write_cfg), "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["issues"] == [1, 2]
    assert data["uplift"]["verdict"] in {"improved", "regressed", "neutral", "inconclusive"}


def test_eval_passes_issue_list_to_harness(write_cfg, monkeypatch):
    captured = {}

    def _spy(issues, run_fn, **kw):
        captured["issues"] = issues
        return _eval_report()

    monkeypatch.setattr("kagura_engineer.cli.run_ab_eval", _spy)
    runner.invoke(app, ["eval", "7", "8", "9", "--config", str(write_cfg)])
    assert captured["issues"] == [7, 8, 9]


def test_eval_run_fn_threads_ground_to_run_idea(write_cfg, monkeypatch):
    captured = {}

    def _spy_run_idea(cfg, issue, **kw):
        captured["issue"] = issue
        captured["ground"] = kw.get("ground")
        return RunReport(issue=issue, phases=[PhaseResult("ship", RunStatus.OK, "x", verdict="green")],
                         pr_url="https://x/pull/1")

    def _spy_eval(issues, run_fn, **kw):
        # invoke the closure the CLI built for the control arm
        run_fn(issues[0], ground=False)
        return _eval_report()

    monkeypatch.setattr("kagura_engineer.cli.run_idea", _spy_run_idea)
    monkeypatch.setattr("kagura_engineer.cli.run_ab_eval", _spy_eval)
    runner.invoke(app, ["eval", "5", "--config", str(write_cfg)])
    assert captured["issue"] == 5
    assert captured["ground"] is False     # the control arm disables grounding


def test_eval_review_off_by_default(write_cfg, monkeypatch):
    captured = {}

    def _spy(issues, run_fn, *, review_fn=None, **kw):
        captured["review_fn"] = review_fn
        return _eval_report()

    monkeypatch.setattr("kagura_engineer.cli.run_ab_eval", _spy)
    runner.invoke(app, ["eval", "1", "--config", str(write_cfg)])
    assert captured["review_fn"] is None   # run-only signals unless --review


def test_eval_review_flag_wires_review_fn(write_cfg, monkeypatch):
    captured = {}

    def _spy(issues, run_fn, *, review_fn=None, **kw):
        captured["review_fn"] = review_fn
        return _eval_report()

    monkeypatch.setattr("kagura_engineer.cli.run_ab_eval", _spy)
    runner.invoke(app, ["eval", "1", "--config", str(write_cfg), "--review"])
    assert captured["review_fn"] is not None
