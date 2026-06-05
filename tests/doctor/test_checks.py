import subprocess

import pytest

from kagura_engineer.doctor import checks
from kagura_engineer.doctor.result import Status


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_git_ok_inside_repo(monkeypatch):
    monkeypatch.setattr(checks.shutil, "which", lambda _: "/usr/bin/git")
    monkeypatch.setattr(
        checks.subprocess, "run", lambda *a, **k: _completed(0, "true\n")
    )
    r = checks.check_git()
    assert r.status is Status.OK


def test_git_fail_when_missing(monkeypatch):
    monkeypatch.setattr(checks.shutil, "which", lambda _: None)
    r = checks.check_git()
    assert r.status is Status.FAIL
    assert "setup" in r.fix_hint


def test_claude_ok_with_api_key(monkeypatch):
    monkeypatch.setattr(checks.shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(
        checks.subprocess, "run", lambda *a, **k: _completed(0, "1.2.3\n")
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
    r = checks.check_claude_code()
    assert r.status is Status.OK
    assert "api_key" in r.detail


def test_claude_warn_subscription_unverified(monkeypatch):
    monkeypatch.setattr(checks.shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(
        checks.subprocess, "run", lambda *a, **k: _completed(0, "1.2.3\n")
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = checks.check_claude_code()
    assert r.status is Status.WARN
    assert "subscription" in r.detail


def test_claude_fail_when_missing(monkeypatch):
    monkeypatch.setattr(checks.shutil, "which", lambda _: None)
    r = checks.check_claude_code()
    assert r.status is Status.FAIL


def test_gh_ok_when_authed(monkeypatch):
    monkeypatch.setattr(checks.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(checks.subprocess, "run", lambda *a, **k: _completed(0))
    assert checks.check_gh().status is Status.OK


def test_gh_fail_when_not_authed(monkeypatch):
    monkeypatch.setattr(checks.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(
        checks.subprocess, "run", lambda *a, **k: _completed(1, "", "not logged in")
    )
    r = checks.check_gh()
    assert r.status is Status.FAIL
    assert "gh auth login" in r.fix_hint
