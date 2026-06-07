from typer.testing import CliRunner

from kagura_engineer.cli import app
from kagura_engineer.doctor.result import CheckResult, Status
from kagura_engineer.setup.result import SetupReport, StepResult, StepStatus

runner = CliRunner()


def _stub_setup_report(
    *, failed_count: int = 0, needs_user_count: int = 0
) -> SetupReport:
    """Build a SetupReport for the exit-code test matrix.

    `failed_count` and `needs_user_count` let each test pin which
    bucket is populated, so the exit-code decision tree can be
    exercised without bringing up a real run_plan.
    """
    return SetupReport(
        ran=[StepResult("gh", StepStatus.OK, "ok")],
        skipped=[StepResult("ollama-models", StepStatus.SKIPPED, "no models")],
        needs_user=[StepResult("claude-code", StepStatus.NEEDS_USER, "log in", "run `claude`")] * needs_user_count,
        failed=[StepResult("git", StepStatus.FAIL, "install failed", "manual fix")] * failed_count,
        duration_s=0.5,
    )


def test_help_lists_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("run", "doctor", "setup"):
        assert cmd in result.stdout


def test_doctor_json_all_ok(write_cfg, monkeypatch):
    monkeypatch.setattr(
        "kagura_engineer.cli.run_all",
        lambda cfg: [CheckResult("git", Status.OK, "ok")],
    )
    result = runner.invoke(app, ["doctor", "--config", str(write_cfg), "--json"])
    assert result.exit_code == 0
    assert '"overall": "ok"' in result.stdout


def test_doctor_exit_1_on_fail(write_cfg, monkeypatch):
    monkeypatch.setattr(
        "kagura_engineer.cli.run_all",
        lambda cfg: [CheckResult("gh", Status.FAIL, "no auth", "gh auth login")],
    )
    result = runner.invoke(app, ["doctor", "--config", str(write_cfg), "--json"])
    assert result.exit_code == 1


# --- setup: exit code contract -----------------------------------------


def test_setup_exit_0_on_clean_run(write_cfg, monkeypatch):
    monkeypatch.setattr("kagura_engineer.cli.run_plan", lambda *a, **kw: _stub_setup_report())
    result = runner.invoke(app, ["setup", "--config", str(write_cfg)])
    assert result.exit_code == 0


def test_setup_exit_1_on_failed(write_cfg, monkeypatch):
    monkeypatch.setattr(
        "kagura_engineer.cli.run_plan",
        lambda *a, **kw: _stub_setup_report(failed_count=1),
    )
    result = runner.invoke(app, ["setup", "--config", str(write_cfg)])
    assert result.exit_code == 1


def test_setup_exit_2_on_needs_user(write_cfg, monkeypatch):
    monkeypatch.setattr(
        "kagura_engineer.cli.run_plan",
        lambda *a, **kw: _stub_setup_report(needs_user_count=1),
    )
    result = runner.invoke(app, ["setup", "--config", str(write_cfg)])
    assert result.exit_code == 2


def test_setup_exit_1_when_both_failed_and_needs_user(write_cfg, monkeypatch):
    # Plan 2 design doc §1.6 contract:
    #   0 = all OK or SKIPPED
    #   1 = 1+ step FAIL
    #   2 = 1+ step NEEDS_USER (no FAIL present)
    # When BOTH are present, FAIL wins (1), not NEEDS_USER (2).
    monkeypatch.setattr(
        "kagura_engineer.cli.run_plan",
        lambda *a, **kw: _stub_setup_report(failed_count=1, needs_user_count=1),
    )
    result = runner.invoke(app, ["setup", "--config", str(write_cfg)])
    assert result.exit_code == 1


def test_setup_json_emits_setup_report(write_cfg, monkeypatch):
    monkeypatch.setattr("kagura_engineer.cli.run_plan", lambda *a, **kw: _stub_setup_report())
    result = runner.invoke(app, ["setup", "--config", str(write_cfg), "--json"])
    assert result.exit_code == 0
    import json
    data = json.loads(result.stdout)
    assert "ran" in data
    assert "needs_user" in data
    assert "is_blocked" in data
    assert data["is_blocked"] is False


def test_setup_dry_run_propagates(write_cfg, monkeypatch):
    captured = {}

    def _spy(*a, **kw):
        captured.update(kw)
        return _stub_setup_report()

    monkeypatch.setattr("kagura_engineer.cli.run_plan", _spy)
    result = runner.invoke(app, ["setup", "--config", str(write_cfg), "--dry-run"])
    assert result.exit_code == 0
    assert captured.get("dry_run") is True


def test_setup_no_input_propagates(write_cfg, monkeypatch):
    captured = {}

    def _spy(*a, **kw):
        captured.update(kw)
        return _stub_setup_report()

    monkeypatch.setattr("kagura_engineer.cli.run_plan", _spy)
    runner.invoke(app, ["setup", "--config", str(write_cfg), "--no-input"])
    assert captured.get("no_input") is True


def test_setup_fix_filter_propagates(write_cfg, monkeypatch):
    captured = {}

    def _spy(*a, **kw):
        captured.update(kw)
        return _stub_setup_report()

    monkeypatch.setattr("kagura_engineer.cli.run_plan", _spy)
    runner.invoke(app, ["setup", "--config", str(write_cfg), "--fix", "gh"])
    assert captured.get("only") == "gh"


def test_setup_unknown_fix_is_clean_error(write_cfg, monkeypatch):
    # An unknown --fix name is a user error, not a silent no-op.
    # The CLI must catch this BEFORE calling run_plan so the user
    # gets actionable feedback (otherwise build_plan returns [] and
    # the report looks like a clean run -> misleading).
    from kagura_engineer.setup import build_plan
    from kagura_engineer.cli import _check_fix_name  # helper exposed for test

    # Pass the FILTERED plan to the helper; if the filter returned [],
    # that's exactly the case the helper is meant to flag.
    assert _check_fix_name("bogus", build_plan(only="bogus")) is not None
    assert _check_fix_name(None, build_plan()) is None
    assert _check_fix_name("gh", build_plan(only="gh")) is None


# --- config-error handling mirrors doctor -------------------------------


def test_setup_missing_config_clean_error(tmp_path):
    missing = tmp_path / "nope.yaml"
    result = runner.invoke(app, ["setup", "--config", str(missing)])
    assert result.exit_code == 2
    assert "config" in result.output.lower()


def test_setup_invalid_config_clean_error(tmp_path):
    bad = tmp_path / "repo.yaml"
    bad.write_text("profile: coding\n")
    result = runner.invoke(app, ["setup", "--config", str(bad)])
    assert result.exit_code == 2
    assert "config" in result.output.lower()


def test_run_not_implemented(write_cfg):
    result = runner.invoke(app, ["run", "--config", str(write_cfg)])
    assert result.exit_code != 0
    assert "not implemented" in result.stdout.lower()


def test_doctor_missing_config_clean_error(tmp_path):
    missing = tmp_path / "nope.yaml"
    result = runner.invoke(app, ["doctor", "--config", str(missing)])
    assert result.exit_code == 2
    assert "config" in result.output.lower()


def test_doctor_invalid_config_clean_error(tmp_path):
    bad = tmp_path / "repo.yaml"
    bad.write_text("profile: coding\n")
    result = runner.invoke(app, ["doctor", "--config", str(bad)])
    assert result.exit_code == 2
    assert "config" in result.output.lower()


def test_doctor_malformed_yaml_clean_error(tmp_path):
    bad = tmp_path / "repo.yaml"
    bad.write_text("profile: coding\n\tbad: tab\n")
    result = runner.invoke(app, ["doctor", "--config", str(bad)])
    assert result.exit_code == 2
    assert "config" in result.output.lower() or "yaml" in result.output.lower()
