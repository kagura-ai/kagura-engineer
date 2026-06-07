"""Unit tests for setup.ollama ensure_ollama_up and pull_ollama_models.

The two steps share a daemon (HTTP probe) and a model manifest
(GET /api/tags). The split between them is purely the action:
"is the daemon up?" vs "are the configured models present?".

Daemon-up mock surface:
  - `urllib.request.urlopen` for the /api/tags probe
  - `subprocess.run` for the `ollama serve` start attempt
  - `shutil.which('ollama')` for the binary presence check
  - `subprocess.run` for the `ollama pull <name>` invocations
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from kagura_engineer.setup import ollama as ollama_setup
from kagura_engineer.setup.ollama import ensure_ollama_up, pull_ollama_models
from kagura_engineer.setup.platform import (
    OSKind,
    PkgManagerKind,
    PlatformInfo,
)
from kagura_engineer.setup.result import StepStatus


_LINUX_APT = PlatformInfo(OSKind.LINUX, PkgManagerKind.APT, is_wsl=False, has_sudo=True)
_DARWIN_BREW = PlatformInfo(OSKind.DARWIN, PkgManagerKind.BREW, is_wsl=False, has_sudo=True)


# ---------------------------------------------------------------------------
# install_command
# ---------------------------------------------------------------------------


def test_install_command_linux_apt():
    cmd = ollama_setup.install_command(_LINUX_APT)
    assert cmd == ["sudo", "apt-get", "install", "-y", "ollama"]


def test_install_command_darwin_brew():
    cmd = ollama_setup.install_command(_DARWIN_BREW)
    assert cmd == ["brew", "install", "ollama"]


def test_install_command_unsupported_returns_none():
    info = PlatformInfo(OSKind.LINUX, PkgManagerKind.NONE, is_wsl=False, has_sudo=True)
    assert ollama_setup.install_command(info) is None


# ---------------------------------------------------------------------------
# ensure_ollama_up
# ---------------------------------------------------------------------------


def _make_urlopen(payload):
    class _Resp:
        def __init__(self, p):
            self._p = p

        def read(self):
            return json.dumps(self._p).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return lambda *a, **k: _Resp(payload)


def test_ensure_ollama_up_already_running(monkeypatch):
    monkeypatch.setattr(ollama_setup.shutil, "which", lambda n: "/usr/bin/ollama" if n == "ollama" else None)
    monkeypatch.setattr(ollama_setup.urllib.request, "urlopen", _make_urlopen({"models": []}))
    r = ensure_ollama_up(_LINUX_APT, "http://localhost:11434", no_input=False, dry_run=False)
    assert r.status is StepStatus.OK
    assert "running" in r.detail.lower() or "reachable" in r.detail.lower()


def test_ensure_ollama_up_dry_run_does_not_serve(monkeypatch):
    monkeypatch.setattr(ollama_setup.shutil, "which", lambda n: "/usr/bin/ollama" if n == "ollama" else None)
    monkeypatch.setattr(ollama_setup.urllib.request, "urlopen", _make_urlopen({"models": []}))
    # The probe happens in dry-run too (we want to know if it's running);
    # but we should NOT spawn a daemon in the background.
    def _must_not_popen(*a, **k):
        raise AssertionError("dry-run must not spawn ollama serve")

    monkeypatch.setattr(ollama_setup.subprocess, "Popen", _must_not_popen)
    monkeypatch.setattr(ollama_setup.time, "monotonic", lambda: 1.0)
    r = ensure_ollama_up(_LINUX_APT, "http://localhost:11434", no_input=False, dry_run=True)
    assert r.status is StepStatus.OK


def test_ensure_ollama_up_daemon_down_needs_user_when_no_serve(monkeypatch):
    # ollama binary is present, but daemon is down, and the serve
    # attempt fails. We surface NEEDS_USER (manual `ollama serve`)
    # rather than auto-blocking on Popen.
    monkeypatch.setattr(ollama_setup.shutil, "which", lambda n: "/usr/bin/ollama" if n == "ollama" else None)
    # urlopen always raises (daemon down)
    def _fail(*a, **k):
        raise ollama_setup.urllib.error.URLError("connection refused")

    monkeypatch.setattr(ollama_setup.urllib.request, "urlopen", _fail)
    # subprocess.Popen is called only inside the serve-attempt branch;
    # we make it raise to exercise the "serve failed" path.
    def _popen_fail(*a, **k):
        raise OSError("cannot fork")

    monkeypatch.setattr(ollama_setup.subprocess, "Popen", _popen_fail)
    monkeypatch.setattr(ollama_setup.time, "monotonic", lambda: 1.0)
    r = ensure_ollama_up(_LINUX_APT, "http://localhost:11434", no_input=False, dry_run=False)
    assert r.status is StepStatus.NEEDS_USER
    assert r.fix_hint is not None
    assert "ollama serve" in r.fix_hint.lower() or "install" in r.fix_hint.lower()


def test_ensure_ollama_up_no_input_escalates_to_fail(monkeypatch):
    monkeypatch.setattr(ollama_setup.shutil, "which", lambda n: "/usr/bin/ollama" if n == "ollama" else None)
    monkeypatch.setattr(ollama_setup.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(ollama_setup.urllib.error.URLError("down")))
    monkeypatch.setattr(ollama_setup.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(OSError("no fork")))
    monkeypatch.setattr(ollama_setup.time, "monotonic", lambda: 1.0)
    r = ensure_ollama_up(_LINUX_APT, "http://localhost:11434", no_input=True, dry_run=False)
    assert r.status is StepStatus.FAIL
    assert r.fix_hint is not None


def test_ensure_ollama_up_dry_run_daemon_down_surfaces_preview(monkeypatch):
    # Probe first; if daemon is down, dry-run surfaces a preview
    # (the install command that would run).
    monkeypatch.setattr(ollama_setup.shutil, "which", lambda n: None)
    monkeypatch.setattr(ollama_setup.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(ollama_setup.urllib.error.URLError("down")))
    def _must_not_popen(*a, **k):
        raise AssertionError("dry-run must not spawn ollama serve")

    monkeypatch.setattr(ollama_setup.subprocess, "Popen", _must_not_popen)
    r = ensure_ollama_up(_LINUX_APT, "http://localhost:11434", no_input=False, dry_run=True)
    assert r.status is StepStatus.OK
    assert "would" in r.detail.lower() or "preview" in r.detail.lower()


# ---------------------------------------------------------------------------
# pull_ollama_models
# ---------------------------------------------------------------------------


def test_pull_skipped_when_no_models_configured(monkeypatch):
    monkeypatch.setattr(ollama_setup.shutil, "which", lambda n: "/usr/bin/ollama" if n == "ollama" else None)
    monkeypatch.setattr(ollama_setup.urllib.request, "urlopen", _make_urlopen({"models": []}))
    r = pull_ollama_models(_LINUX_APT, "http://localhost:11434", required=[], no_input=False, dry_run=False)
    assert r.status is StepStatus.SKIPPED
    assert "no models" in r.detail.lower()


def test_pull_all_present_is_ok(monkeypatch):
    monkeypatch.setattr(ollama_setup.shutil, "which", lambda n: "/usr/bin/ollama" if n == "ollama" else None)
    payload = {"models": [{"name": "qwen2.5-coder:7b"}, {"name": "haiku"}]}
    monkeypatch.setattr(ollama_setup.urllib.request, "urlopen", _make_urlopen(payload))
    r = pull_ollama_models(
        _LINUX_APT, "http://localhost:11434",
        required=["qwen2.5-coder:7b", "haiku"],
        no_input=False, dry_run=False,
    )
    assert r.status is StepStatus.OK


def test_pull_pulls_missing_models(monkeypatch):
    monkeypatch.setattr(ollama_setup.shutil, "which", lambda n: "/usr/bin/ollama" if n == "ollama" else None)
    # First call to urlopen (the /api/tags probe) returns only one of
    # the two required models; the rest must be pulled.
    probes = [
        _make_urlopen({"models": [{"name": "qwen2.5-coder:7b"}]})(),
    ]
    call_log = []
    monkeypatch.setattr(ollama_setup.urllib.request, "urlopen", lambda *a, **k: probes[0])
    monkeypatch.setattr(
        ollama_setup.subprocess,
        "run",
        lambda cmd, **kw: (call_log.append(cmd), subprocess.CompletedProcess(cmd, 0, "ok", ""))[-1],
    )
    monkeypatch.setattr(ollama_setup.time, "monotonic", lambda: 1.0)
    r = pull_ollama_models(
        _LINUX_APT, "http://localhost:11434",
        required=["qwen2.5-coder:7b", "haiku"],
        no_input=False, dry_run=False,
    )
    # The pull step ran for the missing 'haiku' model.
    assert any("pull" in c and "haiku" in c for c in call_log)
    assert r.status is StepStatus.OK


def test_pull_untagged_config_matches_tagged_daemon_model(monkeypatch):
    # setup must agree with doctor's check_ollama: an untagged required name
    # (`qwen2.5-coder`) matches a tagged daemon entry (`qwen2.5-coder:7b`),
    # so no spurious re-pull of an already-present model happens.
    monkeypatch.setattr(ollama_setup.shutil, "which", lambda n: "/usr/bin/ollama" if n == "ollama" else None)
    monkeypatch.setattr(
        ollama_setup.urllib.request, "urlopen",
        _make_urlopen({"models": [{"name": "qwen2.5-coder:7b"}]}),
    )

    def _must_not_run(*a, **k):
        raise AssertionError("must not pull a model doctor considers present")

    monkeypatch.setattr(ollama_setup.subprocess, "run", _must_not_run)
    r = pull_ollama_models(
        _LINUX_APT, "http://localhost:11434",
        required=["qwen2.5-coder"],
        no_input=False, dry_run=False,
    )
    assert r.status is StepStatus.OK


def test_pull_no_input_pulls_silently(monkeypatch):
    # In no-input mode, missing models are still pulled (the daemon
    # is reachable; pulling is non-interactive).
    monkeypatch.setattr(ollama_setup.shutil, "which", lambda n: "/usr/bin/ollama" if n == "ollama" else None)
    monkeypatch.setattr(ollama_setup.urllib.request, "urlopen",
                        _make_urlopen({"models": []}))
    calls = []
    monkeypatch.setattr(
        ollama_setup.subprocess,
        "run",
        lambda cmd, **kw: (calls.append(cmd), subprocess.CompletedProcess(cmd, 0, "ok", ""))[-1],
    )
    monkeypatch.setattr(ollama_setup.time, "monotonic", lambda: 1.0)
    r = pull_ollama_models(
        _LINUX_APT, "http://localhost:11434",
        required=["haiku"],
        no_input=True, dry_run=False,
    )
    assert r.status is StepStatus.OK
    assert any("pull" in c and "haiku" in c for c in calls)


def test_pull_dry_run_does_not_pull(monkeypatch):
    monkeypatch.setattr(ollama_setup.shutil, "which", lambda n: "/usr/bin/ollama" if n == "ollama" else None)
    monkeypatch.setattr(ollama_setup.urllib.request, "urlopen", _make_urlopen({"models": []}))
    def _must_not_run(*a, **k):
        raise AssertionError("dry-run must not invoke ollama pull")

    monkeypatch.setattr(ollama_setup.subprocess, "run", _must_not_run)
    r = pull_ollama_models(
        _LINUX_APT, "http://localhost:11434",
        required=["haiku"],
        no_input=False, dry_run=True,
    )
    assert r.status is StepStatus.OK
    assert "would" in r.detail.lower() or "preview" in r.detail.lower()


def test_pull_fail_on_subprocess_error(monkeypatch):
    monkeypatch.setattr(ollama_setup.shutil, "which", lambda n: "/usr/bin/ollama" if n == "ollama" else None)
    monkeypatch.setattr(ollama_setup.urllib.request, "urlopen", _make_urlopen({"models": []}))
    monkeypatch.setattr(
        ollama_setup.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="network error"),
    )
    monkeypatch.setattr(ollama_setup.time, "monotonic", lambda: 1.0)
    r = pull_ollama_models(
        _LINUX_APT, "http://localhost:11434",
        required=["haiku"],
        no_input=False, dry_run=False,
    )
    assert r.status is StepStatus.FAIL
    assert r.fix_hint is not None
