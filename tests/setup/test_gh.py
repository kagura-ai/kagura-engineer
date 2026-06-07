"""Unit tests for setup.gh ensure_gh_auth.

Distinct from claude.py: the auth question here is *not* a static
file/env check. `gh auth status` is a sub-command that talks to
GitHub and reports the active account. The token-passthrough branch
(env GITHUB_TOKEN / GH_TOKEN) is the only "we don't need a daemon
to confirm" path.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from kagura_engineer.setup import gh as gh_setup
from kagura_engineer.setup.gh import ensure_gh_auth
from kagura_engineer.setup.platform import (
    OSKind,
    PkgManagerKind,
    PlatformInfo,
)
from kagura_engineer.setup.result import StepStatus


# --- install_command construction ---------------------------------------


def test_install_command_linux_apt():
    cmd = gh_setup.install_command(
        PlatformInfo(OSKind.LINUX, PkgManagerKind.APT, is_wsl=False, has_sudo=True)
    )
    assert cmd == ["sudo", "apt-get", "install", "-y", "gh"]


def test_install_command_darwin_brew():
    cmd = gh_setup.install_command(
        PlatformInfo(OSKind.DARWIN, PkgManagerKind.BREW, is_wsl=False, has_sudo=True)
    )
    assert cmd == ["brew", "install", "gh"]


def test_install_command_windows_uses_winget():
    cmd = gh_setup.install_command(
        PlatformInfo(OSKind.WINDOWS, PkgManagerKind.WINGET, is_wsl=False, has_sudo=True)
    )
    assert cmd == ["winget", "install", "--id", "GitHub.cli", "-e", "--source", "winget"]


def test_install_command_unsupported_returns_none():
    info = PlatformInfo(OSKind.LINUX, PkgManagerKind.NONE, is_wsl=False, has_sudo=True)
    assert gh_setup.install_command(info) is None


# --- gh already installed -----------------------------------------------


def test_gh_present_gh_auth_status_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(gh_setup.shutil, "which", lambda n: "/usr/bin/gh" if n == "gh" else None)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    monkeypatch.setattr(
        gh_setup.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="logged in to github.com", stderr=""),
    )

    r = ensure_gh_auth(PlatformInfo(OSKind.LINUX, PkgManagerKind.APT, is_wsl=False, has_sudo=True),
                      no_input=False, dry_run=False)
    assert r.status is StepStatus.OK
    assert "gh" in r.detail.lower() or "github" in r.detail.lower()


# --- gh installed but no auth -------------------------------------------


def test_gh_present_but_not_authed_needs_user(monkeypatch, tmp_path):
    monkeypatch.setattr(gh_setup.shutil, "which", lambda n: "/usr/bin/gh" if n == "gh" else None)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(
        gh_setup.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="You are not logged into any GitHub hosts", stderr=""),
    )

    r = ensure_gh_auth(PlatformInfo(OSKind.LINUX, PkgManagerKind.APT, is_wsl=False, has_sudo=True),
                      no_input=False, dry_run=False)
    assert r.status is StepStatus.NEEDS_USER
    assert r.fix_hint is not None
    assert "gh auth" in r.fix_hint.lower() or "GITHUB_TOKEN" in r.fix_hint


def test_gh_present_but_not_authed_no_input_fails_loud(monkeypatch, tmp_path):
    monkeypatch.setattr(gh_setup.shutil, "which", lambda n: "/usr/bin/gh" if n == "gh" else None)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(
        gh_setup.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr=""),
    )

    r = ensure_gh_auth(PlatformInfo(OSKind.LINUX, PkgManagerKind.APT, is_wsl=False, has_sudo=True),
                      no_input=True, dry_run=False)
    assert r.status is StepStatus.FAIL
    assert r.fix_hint is not None
    assert "no-input" in r.fix_hint or "GITHUB_TOKEN" in r.fix_hint


# --- token passthrough --------------------------------------------------


def test_github_token_env_satisfies_auth(monkeypatch, tmp_path):
    monkeypatch.setattr(gh_setup.shutil, "which", lambda n: "/usr/bin/gh" if n == "gh" else None)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_fake")
    # `gh auth status` should not even be called when env is set.
    def _must_not_run(*a, **k):
        raise AssertionError("token-passthrough must not invoke gh auth status")

    monkeypatch.setattr(gh_setup.subprocess, "run", _must_not_run)

    r = ensure_gh_auth(PlatformInfo(OSKind.LINUX, PkgManagerKind.APT, is_wsl=False, has_sudo=True),
                      no_input=False, dry_run=False)
    assert r.status is StepStatus.OK
    assert "token" in r.detail.lower() or "GITHUB_TOKEN" in r.detail


def test_gh_token_env_satisfies_auth(monkeypatch, tmp_path):
    monkeypatch.setattr(gh_setup.shutil, "which", lambda n: "/usr/bin/gh" if n == "gh" else None)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", "ghp_fake")

    def _must_not_run(*a, **k):
        raise AssertionError("token-passthrough must not invoke gh auth status")

    monkeypatch.setattr(gh_setup.subprocess, "run", _must_not_run)

    r = ensure_gh_auth(PlatformInfo(OSKind.LINUX, PkgManagerKind.APT, is_wsl=False, has_sudo=True),
                      no_input=False, dry_run=False)
    assert r.status is StepStatus.OK


def test_empty_github_token_falls_through_to_gh_status(monkeypatch):
    monkeypatch.setattr(gh_setup.shutil, "which", lambda n: "/usr/bin/gh" if n == "gh" else None)
    monkeypatch.setenv("GITHUB_TOKEN", "")  # empty -> fall through
    calls = []
    monkeypatch.setattr(
        gh_setup.subprocess,
        "run",
        lambda cmd, **kw: (calls.append(cmd), subprocess.CompletedProcess(cmd, 0, "ok", ""))[-1],
    )
    r = ensure_gh_auth(PlatformInfo(OSKind.LINUX, PkgManagerKind.APT, is_wsl=False, has_sudo=True),
                      no_input=False, dry_run=False)
    assert r.status is StepStatus.OK
    assert calls == [["gh", "auth", "status"]]


# --- gh not installed, auto-install happy path --------------------------


def test_gh_not_present_apt_install(monkeypatch):
    present = {"v": False}

    def _which(name):
        return "/usr/bin/gh" if (name == "gh" and present["v"]) else None

    monkeypatch.setattr(gh_setup.shutil, "which", _which)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    calls = []
    monkeypatch.setattr(
        gh_setup.subprocess,
        "run",
        lambda cmd, **kw: (calls.append(cmd), present.update(v=True), subprocess.CompletedProcess(cmd, 0, "ok", ""))[-1],
    )
    monkeypatch.setattr(gh_setup.time, "monotonic", lambda: 1.0)

    r = ensure_gh_auth(PlatformInfo(OSKind.LINUX, PkgManagerKind.APT, is_wsl=False, has_sudo=True),
                      no_input=False, dry_run=False)
    # install OK, but `gh auth status` is not auth'd (default response
    # from the stub is exit 0 with "logged in", so we get OK here).
    # To assert install path, check the install command fired.
    assert any("apt-get" in c for c in calls if isinstance(c, list))


# --- dry-run ------------------------------------------------------------


def test_gh_dry_run_does_not_install(monkeypatch):
    monkeypatch.setattr(gh_setup.shutil, "which", lambda n: None)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    def _must_not_run(*a, **k):
        raise AssertionError("dry-run must not invoke subprocess")

    monkeypatch.setattr(gh_setup.subprocess, "run", _must_not_run)

    r = ensure_gh_auth(PlatformInfo(OSKind.LINUX, PkgManagerKind.APT, is_wsl=False, has_sudo=True),
                      no_input=False, dry_run=True)
    assert r.status is StepStatus.OK
    assert "would" in r.detail.lower() or "preview" in r.detail.lower()


# --- gh auth status subprocess raises -----------------------------------


def test_gh_status_subprocess_raises_is_fail(monkeypatch):
    monkeypatch.setattr(gh_setup.shutil, "which", lambda n: "/usr/bin/gh" if n == "gh" else None)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    def _raise(*a, **k):
        raise subprocess.SubprocessError("spawn failed")

    monkeypatch.setattr(gh_setup.subprocess, "run", _raise)
    monkeypatch.setattr(gh_setup.time, "monotonic", lambda: 1.0)

    r = ensure_gh_auth(PlatformInfo(OSKind.LINUX, PkgManagerKind.APT, is_wsl=False, has_sudo=True),
                      no_input=False, dry_run=False)
    assert r.status is StepStatus.FAIL
    assert r.detail  # has some explanation


# --- not present + no install strategy ----------------------------------


def test_gh_not_present_no_sudo_needs_user(monkeypatch):
    monkeypatch.setattr(gh_setup.shutil, "which", lambda n: None)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    r = ensure_gh_auth(PlatformInfo(OSKind.LINUX, PkgManagerKind.APT, is_wsl=False, has_sudo=False),
                      no_input=False, dry_run=False)
    assert r.status is StepStatus.NEEDS_USER
    assert r.fix_hint is not None
    assert "install" in r.fix_hint.lower() or "gh cli" in r.fix_hint.lower() or "https://" in r.fix_hint.lower()
