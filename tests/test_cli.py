from typer.testing import CliRunner

from kagura_engineer.cli import app
from kagura_engineer.doctor.result import CheckResult, Status

runner = CliRunner()


def _write_cfg(tmp_path):
    p = tmp_path / "repo.yaml"
    p.write_text(
        "profile: coding\n"
        "memory_cloud_url: https://memory.kagura-ai.com\n"
        "context_id: 550e8400-e29b-41d4-a716-446655440000\n"
    )
    return p


def test_help_lists_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("run", "doctor", "setup"):
        assert cmd in result.stdout


def test_doctor_json_all_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "kagura_engineer.cli.run_all",
        lambda cfg: [CheckResult("git", Status.OK, "ok")],
    )
    result = runner.invoke(
        app, ["doctor", "--config", str(_write_cfg(tmp_path)), "--json"]
    )
    assert result.exit_code == 0
    assert '"overall": "ok"' in result.stdout


def test_doctor_exit_1_on_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "kagura_engineer.cli.run_all",
        lambda cfg: [CheckResult("gh", Status.FAIL, "no auth", "gh auth login")],
    )
    result = runner.invoke(
        app, ["doctor", "--config", str(_write_cfg(tmp_path)), "--json"]
    )
    assert result.exit_code == 1


def test_setup_not_implemented(tmp_path):
    result = runner.invoke(app, ["setup", "--config", str(_write_cfg(tmp_path))])
    assert result.exit_code != 0
    assert "not implemented" in result.stdout.lower()


def test_run_not_implemented(tmp_path):
    result = runner.invoke(app, ["run", "--config", str(_write_cfg(tmp_path))])
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
