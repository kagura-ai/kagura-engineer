import subprocess

from kagura_engineer.goal import issues


def test_list_milestone_issues_parses_numbers(monkeypatch):
    def _fake(cmd, **kw):
        assert cmd[:3] == ["gh", "issue", "list"]
        assert "--milestone" in cmd and "v0.3" in cmd
        return subprocess.CompletedProcess(cmd, 0, "12\n7\n34\n", "")

    monkeypatch.setattr(issues.subprocess, "run", _fake)
    assert issues.list_milestone_issues("v0.3") == [12, 7, 34]


def test_list_milestone_issues_ignores_non_digit_lines(monkeypatch):
    monkeypatch.setattr(
        issues.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "5\n\n  \nx\n9\n", ""),
    )
    assert issues.list_milestone_issues("m") == [5, 9]


def test_list_milestone_issues_empty(monkeypatch):
    monkeypatch.setattr(
        issues.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    assert issues.list_milestone_issues("m") == []
