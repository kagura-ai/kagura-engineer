from typer.testing import CliRunner

from kagura_engineer.cli import app
from kagura_engineer.doctor.result import CheckResult, Status

runner = CliRunner()


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


def test_setup_not_implemented(write_cfg):
    result = runner.invoke(app, ["setup", "--config", str(write_cfg)])
    assert result.exit_code != 0
    assert "not implemented" in result.stdout.lower()


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
    bad.write_text("profile: coding\n")  # missing required fields
    result = runner.invoke(app, ["doctor", "--config", str(bad)])
    assert result.exit_code == 2
    assert "config" in result.output.lower()


def test_doctor_malformed_yaml_clean_error(tmp_path):
    bad = tmp_path / "repo.yaml"
    bad.write_text("profile: coding\n\tbad: tab\n")
    result = runner.invoke(app, ["doctor", "--config", str(bad)])
    assert result.exit_code == 2
    assert "config" in result.output.lower() or "yaml" in result.output.lower()
