"""Unit tests for setup.git ensure_git.

Mocking strategy: monkeypatch `shutil.which` (to simulate the binary
being absent or present on PATH), and monkeypatch
`subprocess.run` (to capture the install command without actually
invoking the OS package manager). Real installs are not exercised
here — the only test that hits a real subprocess is the
`test_install_command_executes_via_subprocess_run` shape check, and
that one is also stubbed.
"""
from __future__ import annotations

import subprocess
import time

import pytest

from kagura_engineer.setup import git as git_setup
from kagura_engineer.setup.git import ensure_git
from kagura_engineer.setup.platform import (
    OSKind,
    PkgManagerKind,
    PlatformInfo,
)
from kagura_engineer.setup.result import StepResult, StepStatus


# --- install_command construction ---------------------------------------


def test_install_command_linux_apt():
    cmd = git_setup.install_command(
        PlatformInfo(OSKind.LINUX, PkgManagerKind.APT, is_wsl=False, has_sudo=True)
    )
    assert cmd == ["sudo", "apt-get", "install", "-y", "git"]


def test_install_command_darwin_brew_omits_sudo():
    # brew runs as the user, never via sudo.
    cmd = git_setup.install_command(
        PlatformInfo(OSKind.DARWIN, PkgManagerKind.BREW, is_wsl=False, has_sudo=True)
    )
    assert cmd == ["brew", "install", "git"]


def test_install_command_linux_dnf():
    cmd = git_setup.install_command(
        PlatformInfo(OSKind.LINUX, PkgManagerKind.DNF, is_wsl=False, has_sudo=True)
    )
    assert cmd == ["sudo", "dnf", "install", "-y", "git"]


def test_install_command_linux_pacman_skips_y_flag():
    # pacman does not have -y; it prompts by default and -y/--noconfirm
    # is separate. We use --noconfirm explicitly so a sudo apt-style
    # `-y` is not silently dropped.
    cmd = git_setup.install_command(
        PlatformInfo(OSKind.LINUX, PkgManagerKind.PACMAN, is_wsl=False, has_sudo=True)
    )
    assert cmd == ["sudo", "pacman", "-S", "--noconfirm", "git"]


def test_install_command_unsupported_returns_none():
    info = PlatformInfo(OSKind.WINDOWS, PkgManagerKind.WINGET, is_wsl=False, has_sudo=True)
    assert git_setup.install_command(info) is None
    info2 = PlatformInfo(OSKind.LINUX, PkgManagerKind.NONE, is_wsl=False, has_sudo=True)
    assert git_setup.install_command(info2) is None


# --- ensure_git: already installed --------------------------------------


def test_ensure_git_returns_ok_when_already_installed(monkeypatch):
    monkeypatch.setattr(git_setup.shutil, "which", lambda name: "/usr/bin/git" if name == "git" else None)
    # subprocess.run must NOT be called.
    def _must_not_run(*a, **k):
        raise AssertionError("subprocess.run should not be called when git is on PATH")

    monkeypatch.setattr(git_setup.subprocess, "run", _must_not_run)

    r = ensure_git(
        PlatformInfo(OSKind.LINUX, PkgManagerKind.APT, is_wsl=False, has_sudo=True),
        no_input=False,
        dry_run=False,
    )
    assert r.status is StepStatus.OK
    assert "already" in r.detail.lower()


# --- ensure_git: auto-install happy path -------------------------------


def test_ensure_git_auto_installs_on_linux_apt(monkeypatch):
    # git absent BEFORE install, present AFTER (simulates the package
    # manager dropping the binary on PATH).
    present = {"v": False}

    def _which(name):
        return "/usr/bin/git" if (name == "git" and present["v"]) else None

    monkeypatch.setattr(git_setup.shutil, "which", _which)
    calls = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        present["v"] = True
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(git_setup.subprocess, "run", _fake_run)
    monkeypatch.setattr(git_setup.time, "monotonic", lambda: 100.0 + 0.5 * len(calls))

    r = ensure_git(
        PlatformInfo(OSKind.LINUX, PkgManagerKind.APT, is_wsl=False, has_sudo=True),
        no_input=False,
        dry_run=False,
    )
    assert r.status is StepStatus.OK
    assert r.duration_s >= 0.0
    assert calls == [["sudo", "apt-get", "install", "-y", "git"]]


def test_ensure_git_auto_installs_on_darwin_brew(monkeypatch):
    present = {"v": False}

    def _which(name):
        return "/opt/homebrew/bin/git" if (name == "git" and present["v"]) else None

    monkeypatch.setattr(git_setup.shutil, "which", _which)
    calls = []
    monkeypatch.setattr(
        git_setup.subprocess,
        "run",
        lambda cmd, **kw: (calls.append(cmd), present.update(v=True), subprocess.CompletedProcess(cmd, 0, "", ""))[-1],
    )
    monkeypatch.setattr(git_setup.time, "monotonic", lambda: 1.0)

    r = ensure_git(
        PlatformInfo(OSKind.DARWIN, PkgManagerKind.BREW, is_wsl=False, has_sudo=True),
        no_input=False,
        dry_run=False,
    )
    assert r.status is StepStatus.OK
    assert calls == [["brew", "install", "git"]]


# --- ensure_git: dry-run -----------------------------------------------


def test_ensure_git_dry_run_does_not_execute(monkeypatch):
    monkeypatch.setattr(git_setup.shutil, "which", lambda name: None)
    def _must_not_run(*a, **k):
        raise AssertionError("dry-run must not invoke subprocess")

    monkeypatch.setattr(git_setup.subprocess, "run", _must_not_run)

    r = ensure_git(
        PlatformInfo(OSKind.LINUX, PkgManagerKind.APT, is_wsl=False, has_sudo=True),
        no_input=False,
        dry_run=True,
    )
    # dry-run on an auto-installable platform => OK with a "would run"
    # detail, because the user asked us to preview, not act.
    assert r.status is StepStatus.OK
    assert "would" in r.detail.lower() or "preview" in r.detail.lower()


# --- ensure_git: NEEDS_USER paths --------------------------------------


def test_ensure_git_needs_user_when_no_sudo(monkeypatch):
    monkeypatch.setattr(git_setup.shutil, "which", lambda name: None)
    monkeypatch.setattr(git_setup.subprocess, "run", lambda *a, **k: None)

    r = ensure_git(
        PlatformInfo(OSKind.LINUX, PkgManagerKind.APT, is_wsl=False, has_sudo=False),
        no_input=False,
        dry_run=False,
    )
    assert r.status is StepStatus.NEEDS_USER
    assert r.fix_hint is not None
    assert "sudo" in r.fix_hint.lower() or "apt" in r.fix_hint.lower()


def test_ensure_git_needs_user_when_windows(monkeypatch):
    monkeypatch.setattr(git_setup.shutil, "which", lambda name: None)
    # subprocess.run must not be called on the NEEDS_USER path.
    def _must_not_run(*a, **k):
        raise AssertionError("Windows should not auto-install; subprocess.run must not fire")

    monkeypatch.setattr(git_setup.subprocess, "run", _must_not_run)

    r = ensure_git(
        PlatformInfo(OSKind.WINDOWS, PkgManagerKind.WINGET, is_wsl=False, has_sudo=True),
        no_input=False,
        dry_run=False,
    )
    assert r.status is StepStatus.NEEDS_USER
    assert r.fix_hint is not None
    assert "winget" in r.fix_hint.lower() or "git-scm" in r.fix_hint.lower()


def test_ensure_git_no_input_fails_loudly_when_user_input_needed(monkeypatch):
    # --no-input must turn a NEEDS_USER into FAIL, not silently install
    # and not silently NEEDS_USER (which would make the exit code
    # ambiguous).
    monkeypatch.setattr(git_setup.shutil, "which", lambda name: None)
    monkeypatch.setattr(git_setup.subprocess, "run", lambda *a, **k: None)

    r = ensure_git(
        PlatformInfo(OSKind.LINUX, PkgManagerKind.APT, is_wsl=False, has_sudo=False),
        no_input=True,
        dry_run=False,
    )
    assert r.status is StepStatus.FAIL
    assert r.fix_hint is not None
    assert "--no-input" in r.fix_hint or "sudo" in r.fix_hint.lower()


# --- ensure_git: FAIL on install error ---------------------------------


def test_ensure_git_fail_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(git_setup.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        git_setup.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 100, stdout="", stderr="E: unable to locate"),
    )
    monkeypatch.setattr(git_setup.time, "monotonic", lambda: 1.0)

    r = ensure_git(
        PlatformInfo(OSKind.LINUX, PkgManagerKind.APT, is_wsl=False, has_sudo=True),
        no_input=False,
        dry_run=False,
    )
    assert r.status is StepStatus.FAIL
    assert "100" in r.detail or "exit" in r.detail.lower()
    assert r.fix_hint is not None


def test_ensure_git_fail_when_subprocess_raises(monkeypatch):
    monkeypatch.setattr(git_setup.shutil, "which", lambda name: None)
    def _raise(*a, **k):
        raise subprocess.SubprocessError("spawn failed")

    monkeypatch.setattr(git_setup.subprocess, "run", _raise)
    monkeypatch.setattr(git_setup.time, "monotonic", lambda: 1.0)

    r = ensure_git(
        PlatformInfo(OSKind.LINUX, PkgManagerKind.APT, is_wsl=False, has_sudo=True),
        no_input=False,
        dry_run=False,
    )
    assert r.status is StepStatus.FAIL
    assert "spawn failed" in r.detail or "subprocess" in r.detail.lower()
