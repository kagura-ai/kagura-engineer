"""Unit tests for the shared install helper used by every step that
runs a package-manager command (`git`, `gh`, future `ollama`).

The helper is the one place that decides:
  - how to time the install (start = time.monotonic())
  - how to translate exceptions to StepStatus.FAIL
  - how to post-verify the binary is on PATH after install
  - the dry-run preview shape

The intent is that each per-step module keeps its own
`install_command(platform)` table and its own pre-install check
(usually `shutil.which` for the target binary), but delegates the
'run the install command and report a StepResult' boilerplate to
`run_install`.

We do NOT collapse every step into the helper: the auth-only and
verify-only steps (claude.py auth, gh auth status) stay in the
per-step file because the helper's shape is wrong for them.
"""
from __future__ import annotations

import subprocess
import time

import pytest

from kagura_engineer.setup.install import (
    NEEDS_USER_HINT_PKG_MANAGER,
    NEEDS_USER_HINT_WINDOWS,
    run_install,
)
from kagura_engineer.setup.platform import (
    OSKind,
    PkgManagerKind,
    PlatformInfo,
)
from kagura_engineer.setup.result import StepResult, StepStatus


_LINUX_APT = PlatformInfo(OSKind.LINUX, PkgManagerKind.APT, is_wsl=False, has_sudo=True)
_DARWIN_BREW = PlatformInfo(OSKind.DARWIN, PkgManagerKind.BREW, is_wsl=False, has_sudo=True)
_WINDOWS_WINGET = PlatformInfo(OSKind.WINDOWS, PkgManagerKind.WINGET, is_wsl=False, has_sudo=True)


# --- happy path ---------------------------------------------------------


def test_run_install_returns_ok_on_zero_exit(monkeypatch):
    present = {"v": True}  # binary already on PATH (post-install)
    monkeypatch.setattr("time.monotonic", lambda: 1.0)
    calls = []
    monkeypatch.setattr(
        "subprocess.run",
        lambda cmd, **kw: (calls.append(cmd), subprocess.CompletedProcess(cmd, 0, "installed", ""))[-1],
    )
    r = run_install(
        step_name="git",
        binary="git",
        cmd=["sudo", "apt-get", "install", "-y", "git"],
        platform=_LINUX_APT,
        dry_run=False,
        no_input=False,
    )
    assert r.status is StepStatus.OK
    assert "sudo apt-get install" in r.detail
    assert calls == [["sudo", "apt-get", "install", "-y", "git"]]
    assert r.duration_s == 0.0  # both monotonic calls pinned to 1.0


# --- dry-run ------------------------------------------------------------


def test_run_install_dry_run_does_not_call_subprocess(monkeypatch):
    def _must_not_run(*a, **k):
        raise AssertionError("dry-run must not invoke subprocess")

    monkeypatch.setattr("subprocess.run", _must_not_run)
    monkeypatch.setattr("time.monotonic", lambda: 1.0)

    r = run_install(
        step_name="git",
        binary="git",
        cmd=["sudo", "apt-get", "install", "-y", "git"],
        platform=_LINUX_APT,
        dry_run=True,
        no_input=False,
    )
    assert r.status is StepStatus.OK
    assert "would" in r.detail.lower()
    assert "sudo apt-get install" in r.detail


# --- post-install verification -------------------------------------------


def test_run_install_fail_when_post_verify_binary_missing(monkeypatch):
    # The install command exits 0, but `git` is not on PATH afterwards
    # (a real scenario: apt installed to /usr/local/bin which the
    # current shell has not refreshed).
    monkeypatch.setattr("time.monotonic", lambda: 1.0)
    monkeypatch.setattr(
        "subprocess.run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "ok", ""),
    )
    # shutil.which('git') -> None
    monkeypatch.setattr("shutil.which", lambda n: None)

    r = run_install(
        step_name="git",
        binary="git",
        cmd=["sudo", "apt-get", "install", "-y", "git"],
        platform=_LINUX_APT,
        dry_run=False,
        no_input=False,
    )
    assert r.status is StepStatus.FAIL
    assert "PATH" in r.detail or "shell" in r.detail
    assert r.fix_hint is not None


def test_run_install_post_verify_skipped_when_binary_none(monkeypatch):
    # Some install commands don't have a single binary to verify
    # (e.g. ollama is a daemon, not a CLI on PATH). The caller passes
    # binary=None to opt out of the post-check.
    monkeypatch.setattr("time.monotonic", lambda: 1.0)
    monkeypatch.setattr(
        "subprocess.run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "ok", ""),
    )
    r = run_install(
        step_name="ollama-server",
        binary=None,
        cmd=["ollama", "serve", "&"],
        platform=_LINUX_APT,
        dry_run=False,
        no_input=False,
    )
    assert r.status is StepStatus.OK


# --- no command available (caller must have built a list) --------------


def test_run_install_no_command_means_needs_user(monkeypatch):
    monkeypatch.setattr("time.monotonic", lambda: 1.0)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: None)

    r = run_install(
        step_name="gh",
        binary="gh",
        cmd=None,  # no install strategy for this platform
        platform=_LINUX_APT,
        dry_run=False,
        no_input=False,
    )
    assert r.status is StepStatus.NEEDS_USER
    assert r.fix_hint is not None


def test_run_install_no_command_no_input_escalates_to_fail(monkeypatch):
    monkeypatch.setattr("time.monotonic", lambda: 1.0)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: None)

    r = run_install(
        step_name="gh",
        binary="gh",
        cmd=None,
        platform=_LINUX_APT,
        dry_run=False,
        no_input=True,
    )
    assert r.status is StepStatus.FAIL
    assert r.fix_hint is not None
    assert "--no-input" in r.fix_hint or "install" in r.fix_hint.lower()


# --- subprocess failures -----------------------------------------------


def test_run_install_fail_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr("time.monotonic", lambda: 1.0)
    monkeypatch.setattr(
        "subprocess.run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 100, stdout="", stderr="E: unable to locate"),
    )

    r = run_install(
        step_name="git",
        binary="git",
        cmd=["sudo", "apt-get", "install", "-y", "git"],
        platform=_LINUX_APT,
        dry_run=False,
        no_input=False,
    )
    assert r.status is StepStatus.FAIL
    assert "100" in r.detail or "exit" in r.detail.lower()


def test_run_install_fail_on_subprocess_raises(monkeypatch):
    monkeypatch.setattr("time.monotonic", lambda: 1.0)
    def _raise(*a, **k):
        raise subprocess.SubprocessError("spawn failed")

    monkeypatch.setattr("subprocess.run", _raise)

    r = run_install(
        step_name="git",
        binary="git",
        cmd=["sudo", "apt-get", "install", "-y", "git"],
        platform=_LINUX_APT,
        dry_run=False,
        no_input=False,
    )
    assert r.status is StepStatus.FAIL
    assert "spawn failed" in r.detail or "SubprocessError" in r.detail


def test_run_install_fail_on_timeout(monkeypatch):
    monkeypatch.setattr("time.monotonic", lambda: 1.0)
    def _timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd=["apt-get"], timeout=5)

    monkeypatch.setattr("subprocess.run", _timeout)

    r = run_install(
        step_name="git",
        binary="git",
        cmd=["sudo", "apt-get", "install", "-y", "git"],
        platform=_LINUX_APT,
        dry_run=False,
        no_input=False,
    )
    assert r.status is StepStatus.FAIL
    assert "TimeoutExpired" in r.detail or "timed out" in r.detail.lower()


# --- needs_user hint selection ------------------------------------------


def test_needs_user_hint_windows_for_windows_platform():
    assert "winget" in NEEDS_USER_HINT_WINDOWS.lower() or "git-scm" in NEEDS_USER_HINT_WINDOWS.lower()


def test_needs_user_hint_pkg_manager_default_mentions_pkg_managers():
    for pm in ("brew", "apt", "dnf", "pacman"):
        assert pm in NEEDS_USER_HINT_PKG_MANAGER
