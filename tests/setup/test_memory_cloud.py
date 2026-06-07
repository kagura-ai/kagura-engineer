"""Unit tests for setup.memory_cloud ensure_memory_cloud_reachable.

Thin wrapper over doctor.checks.check_memory_cloud. We test the
shape translation (CheckResult -> StepResult) and the
preservation of the doctor's bucket semantics:
  - 2xx -> OK
  - 4xx -> WARN (treated as OK-equivalent; auth verify is Plan 3)
  - unreachable / 5xx -> FAIL
"""
from __future__ import annotations

import urllib.error

import pytest

from kagura_engineer.setup import memory_cloud as mc_setup
from kagura_engineer.setup.memory_cloud import ensure_memory_cloud_reachable
from kagura_engineer.setup.result import StepStatus


def test_ok_on_2xx(monkeypatch):
    payload = {}

    class _Resp:
        def read(self):
            import json
            return json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mc_setup.urllib.request, "urlopen", lambda *a, **k: _Resp())
    r = ensure_memory_cloud_reachable("https://memory.kagura-ai.com", no_input=False, dry_run=False)
    assert r.status is StepStatus.OK
    assert "memory" in r.detail.lower() or "reachable" in r.detail.lower()


def test_ok_on_4xx(monkeypatch):
    # 4xx proves the host is reachable; setup reports OK (its
    # StepStatus enum has no WARN). The detail still names the
    # HTTP code so an operator can tell what the host said.
    def _boom(*a, **k):
        raise urllib.error.HTTPError("https://memory.kagura-ai.com/health", 403, "Forbidden", {}, None)

    monkeypatch.setattr(mc_setup.urllib.request, "urlopen", _boom)
    r = ensure_memory_cloud_reachable("https://memory.kagura-ai.com", no_input=False, dry_run=False)
    assert r.status is StepStatus.OK
    assert "403" in r.detail or "http" in r.detail.lower()


def test_fail_on_unreachable(monkeypatch):
    def _boom(*a, **k):
        raise urllib.error.URLError("dns")

    monkeypatch.setattr(mc_setup.urllib.request, "urlopen", _boom)
    r = ensure_memory_cloud_reachable("https://memory.kagura-ai.com", no_input=False, dry_run=False)
    assert r.status is StepStatus.FAIL
    assert r.fix_hint is not None
    assert "url" in r.fix_hint.lower() or "network" in r.fix_hint.lower()


def test_dry_run_does_not_probe(monkeypatch):
    def _must_not_run(*a, **k):
        raise AssertionError("dry-run must not hit the network")

    monkeypatch.setattr(mc_setup.urllib.request, "urlopen", _must_not_run)
    r = ensure_memory_cloud_reachable("https://memory.kagura-ai.com", no_input=False, dry_run=True)
    assert r.status is StepStatus.OK
    assert "would" in r.detail.lower() or "preview" in r.detail.lower()


def test_no_input_escalates_fail(monkeypatch):
    def _boom(*a, **k):
        raise urllib.error.URLError("dns")

    monkeypatch.setattr(mc_setup.urllib.request, "urlopen", _boom)
    # --no-input on a FAIL stays FAIL (no escalation); this test
    # documents that the WARN bucket is the only one that --no-input
    # touches in this step.
    r = ensure_memory_cloud_reachable("https://memory.kagura-ai.com", no_input=True, dry_run=False)
    assert r.status is StepStatus.FAIL
