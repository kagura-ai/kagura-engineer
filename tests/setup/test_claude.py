"""Unit tests for setup.claude ensure_claude_login.

Covers: already-installed + auth OK, already-installed + auth missing,
not installed (auto-install via curl pipe bash), not installed with
no installer reachable, --no-input loud-FAIL on interactive step.

`ensure_claude_login` deliberately does NOT call `claude` itself to
verify a fresh login. The credential cache is the source of truth for
'subscription login has happened'; a live `claude -p` probe would
spend tokens and require network. The doctor check (check_haiku) is
the right place for a live probe, not setup.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from kagura_engineer.setup import auth as auth_module
from kagura_engineer.setup import claude as claude_setup
from kagura_engineer.setup.claude import ensure_claude_login
from kagura_engineer.setup.result import StepStatus


# --- happy path: installed + auth present ------------------------------


def test_claude_present_with_subscription_cache_is_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_setup.shutil, "which", lambda n: "/usr/bin/claude" if n == "claude" else None)
    # No API key, but a credential cache exists.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cache = tmp_path / ".claude" / ".credentials.json"
    cache.parent.mkdir(parents=True)
    cache.write_text("{}")
    monkeypatch.setattr(auth_module.Path, "home", classmethod(lambda cls: tmp_path))

    r = ensure_claude_login(no_input=False, dry_run=False)
    assert r.status is StepStatus.OK
    assert "subscription" in r.detail.lower() or "credentials" in r.detail.lower()


def test_claude_present_with_env_key_is_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_setup.shutil, "which", lambda n: "/usr/bin/claude" if n == "claude" else None)
    monkeypatch.setattr(os, "environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}, raising=False)
    r = ensure_claude_login(no_input=False, dry_run=False)
    assert r.status is StepStatus.OK


# --- installed but no auth: NEEDS_USER ----------------------------------


def test_claude_present_without_auth_needs_user(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_setup.shutil, "which", lambda n: "/usr/bin/claude" if n == "claude" else None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(auth_module.Path, "home", classmethod(lambda cls: tmp_path))

    r = ensure_claude_login(no_input=False, dry_run=False)
    assert r.status is StepStatus.NEEDS_USER
    assert r.fix_hint is not None
    assert "claude" in r.fix_hint.lower()


def test_claude_present_without_auth_no_input_fails_loud(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_setup.shutil, "which", lambda n: "/usr/bin/claude" if n == "claude" else None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(auth_module.Path, "home", classmethod(lambda cls: tmp_path))

    r = ensure_claude_login(no_input=True, dry_run=False)
    assert r.status is StepStatus.FAIL
    assert r.fix_hint is not None
    assert "--no-input" in r.fix_hint or "interactive" in r.fix_hint.lower()


# --- not installed: try to install --------------------------------------


def test_claude_not_present_no_installer_needed_in_dry_run(monkeypatch, tmp_path):
    # dry-run must NEVER actually download. We assert subprocess.run is
    # not called regardless of curl availability.
    monkeypatch.setattr(claude_setup.shutil, "which", lambda n: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(auth_module.Path, "home", classmethod(lambda cls: tmp_path))

    def _must_not_run(*a, **k):
        raise AssertionError("dry-run must not invoke subprocess")

    monkeypatch.setattr(claude_setup.subprocess, "run", _must_not_run)

    r = ensure_claude_login(no_input=False, dry_run=True)
    # dry-run on the not-installed case is OK (preview) — the user
    # asked for a preview, not an action.
    assert r.status is StepStatus.OK
    assert "would" in r.detail.lower() or "preview" in r.detail.lower()


def test_claude_not_present_no_curl_needs_user(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_setup.shutil, "which", lambda n: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(auth_module.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(claude_setup, "INSTALL_URL", "https://claude.ai/install.sh")
    # Force _curl_available to return False.
    monkeypatch.setattr(claude_setup, "_curl_available", lambda: False)

    r = ensure_claude_login(no_input=False, dry_run=False)
    assert r.status is StepStatus.NEEDS_USER
    assert r.fix_hint is not None
    assert "install" in r.fix_hint.lower() or "https://" in r.fix_hint.lower()


def test_claude_not_present_curl_install_succeeds(monkeypatch, tmp_path):
    """When claude is absent, the installer runs and writes the cache
    the next time the user runs `claude` interactively. We can't
    actually run claude in tests, so we simulate: the install command
    runs, then `claude --version` works (which is what setup probes),
    then the user still needs an interactive login — we surface
    NEEDS_USER with the install hint."""
    present_after_install = {"v": False}

    def _which(name):
        if name == "claude" and present_after_install["v"]:
            return "/usr/bin/claude"
        if name == "curl":
            return "/usr/bin/curl"
        return None

    monkeypatch.setattr(claude_setup.shutil, "which", _which)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(auth_module.Path, "home", classmethod(lambda cls: tmp_path))

    calls = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        # Simulate the installer landing the binary.
        present_after_install["v"] = True
        return subprocess.CompletedProcess(cmd, 0, stdout="installed", stderr="")

    monkeypatch.setattr(claude_setup.subprocess, "run", _fake_run)
    monkeypatch.setattr(claude_setup.time, "monotonic", lambda: 1.0)

    r = ensure_claude_login(no_input=False, dry_run=False)
    # After install, the binary exists but no auth. The install
    # command's "install" verb is satisfied; the auth step is not.
    assert r.status is StepStatus.NEEDS_USER
    assert len(calls) == 1
    # The install command is run via `sh -c`, so subprocess.run gets
    # a string. It must start with the curl invocation.
    assert isinstance(calls[0], str)
    assert calls[0].startswith("curl ")


def test_claude_install_fails_returns_fail(monkeypatch, tmp_path):
    monkeypatch.setattr(
        claude_setup.shutil,
        "which",
        lambda n: "/usr/bin/curl" if n == "curl" else None,
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(auth_module.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        claude_setup.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 22, stdout="", stderr="curl: 404"),
    )
    monkeypatch.setattr(claude_setup.time, "monotonic", lambda: 1.0)

    r = ensure_claude_login(no_input=False, dry_run=False)
    assert r.status is StepStatus.FAIL
    assert "22" in r.detail or "exit" in r.detail.lower()


def test_claude_install_no_input_fails_loud(monkeypatch, tmp_path):
    # --no-input on the install path: we could auto-install (curl|bash
    # is non-interactive), so we DO install. But a fresh install leaves
    # no credential cache, so the post-install auth check is the one
    # that surfaces NEEDS_USER. With --no-input, that becomes FAIL.
    present_after_install = {"v": False}

    def _which(name):
        if name == "claude" and present_after_install["v"]:
            return "/usr/bin/claude"
        if name == "curl":
            return "/usr/bin/curl"
        return None

    monkeypatch.setattr(claude_setup.shutil, "which", _which)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(auth_module.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        claude_setup.subprocess,
        "run",
        lambda cmd, **kw: (present_after_install.update(v=True), subprocess.CompletedProcess(cmd, 0, "", ""))[-1],
    )
    monkeypatch.setattr(claude_setup.time, "monotonic", lambda: 1.0)

    r = ensure_claude_login(no_input=True, dry_run=False)
    assert r.status is StepStatus.FAIL
    assert "interactive" in r.fix_hint.lower() or "login" in r.fix_hint.lower() or "no-input" in r.fix_hint.lower()
