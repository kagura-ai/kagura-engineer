"""Unit tests for the setup orchestrator.

build_plan() and run_plan() are the public entry points. We
exercise them through the public `setup` package surface so
future refactors of the internal step registry do not break the
contract.
"""
from __future__ import annotations

import pytest

from kagura_engineer.setup import (
    STEP_NAMES,
    build_plan,
    run_plan,
)
from kagura_engineer.setup.platform import (
    OSKind,
    PkgManagerKind,
    PlatformInfo,
)
from kagura_engineer.setup.result import SetupReport, StepResult, StepStatus


# --- build_plan: ordering & filtering -----------------------------------


def test_build_plan_returns_all_steps_when_no_only():
    plan = build_plan()
    assert plan == STEP_NAMES


def test_build_plan_filters_by_only():
    plan = build_plan(only="git")
    assert plan == ["git"]


def test_build_plan_ignores_unknown_only():
    plan = build_plan(only="nonexistent")
    assert [s.name for s in plan] == []


def test_step_names_are_in_canonical_order():
    # The orchestrator renders report buckets in the order the steps
    # run; lock that order in.
    assert STEP_NAMES == [
        "git",
        "claude-code",
        "gh",
        "ollama",
        "ollama-models",
        "memory-cloud",
        "memory-mcp",
    ]


# --- run_plan: integration ----------------------------------------------


def test_run_plan_runs_all_steps_and_aggregates(monkeypatch, valid_config):
    calls = []

    def _stub(name, status=StepStatus.OK, detail="stub"):
        def _f(*a, **kw):
            calls.append(name)
            return StepResult(name, status, detail)
        return _f

    monkeypatch.setattr("kagura_engineer.setup.git.ensure_git", _stub("git"))
    monkeypatch.setattr(
        "kagura_engineer.setup.claude.ensure_claude_login",
        _stub("claude-code"),
    )
    monkeypatch.setattr(
        "kagura_engineer.setup.gh.ensure_gh_auth",
        _stub("gh"),
    )
    monkeypatch.setattr(
        "kagura_engineer.setup.ollama.ensure_ollama_up",
        _stub("ollama"),
    )
    monkeypatch.setattr(
        "kagura_engineer.setup.ollama.pull_ollama_models",
        _stub("ollama-models", status=StepStatus.SKIPPED, detail="no models"),
    )
    monkeypatch.setattr(
        "kagura_engineer.setup.memory_cloud.ensure_memory_cloud_reachable",
        _stub("memory-cloud"),
    )
    monkeypatch.setattr(
        "kagura_engineer.setup.memory_mcp.ensure_memory_mcp_config",
        _stub("memory-mcp"),
    )

    report = run_plan(valid_config, no_input=False, dry_run=False)
    assert calls == STEP_NAMES
    assert [r.name for r in report.ran] == [
        "git",
        "claude-code",
        "gh",
        "ollama",
        "memory-cloud",
        "memory-mcp",
    ]
    assert [r.name for r in report.skipped] == ["ollama-models"]
    assert report.failed == []
    assert report.needs_user == []
    assert report.is_blocked is False
    assert report.duration_s >= 0.0


def test_run_plan_only_runs_named_step(monkeypatch, valid_config):
    calls = []

    def _stub(name):
        def _f(*a, **kw):
            calls.append(name)
            return StepResult(name, StepStatus.OK, "stub")
        return _f

    monkeypatch.setattr("kagura_engineer.setup.git.ensure_git", _stub("git"))
    monkeypatch.setattr("kagura_engineer.setup.claude.ensure_claude_login", _stub("claude-code"))
    monkeypatch.setattr("kagura_engineer.setup.gh.ensure_gh_auth", _stub("gh"))
    monkeypatch.setattr("kagura_engineer.setup.ollama.ensure_ollama_up", _stub("ollama"))
    monkeypatch.setattr("kagura_engineer.setup.ollama.pull_ollama_models", _stub("ollama-models"))
    monkeypatch.setattr("kagura_engineer.setup.memory_cloud.ensure_memory_cloud_reachable", _stub("memory-cloud"))

    run_plan(valid_config, no_input=False, dry_run=False, only="gh")
    assert calls == ["gh"]


def test_run_plan_isolates_step_exceptions(monkeypatch, valid_config):
    # A buggy step must not abort the whole plan; the other steps
    # still run. Mirrors the doctor registry.py guard (the same
    # bug-class would otherwise repeat here).
    def _boom(*a, **kw):
        raise KeyError("malformed response")

    monkeypatch.setattr("kagura_engineer.setup.git.ensure_git", _boom)
    monkeypatch.setattr(
        "kagura_engineer.setup.claude.ensure_claude_login",
        lambda **kw: StepResult("claude-code", StepStatus.OK, "ok"),
    )
    monkeypatch.setattr(
        "kagura_engineer.setup.gh.ensure_gh_auth",
        lambda *a, **kw: StepResult("gh", StepStatus.OK, "ok"),
    )
    monkeypatch.setattr(
        "kagura_engineer.setup.ollama.ensure_ollama_up",
        lambda *a, **kw: StepResult("ollama", StepStatus.OK, "ok"),
    )
    monkeypatch.setattr(
        "kagura_engineer.setup.ollama.pull_ollama_models",
        lambda *a, **kw: StepResult("ollama-models", StepStatus.SKIPPED, "no models"),
    )
    monkeypatch.setattr(
        "kagura_engineer.setup.memory_cloud.ensure_memory_cloud_reachable",
        lambda *a, **kw: StepResult("memory-cloud", StepStatus.OK, "ok"),
    )
    monkeypatch.setattr(
        "kagura_engineer.setup.memory_mcp.ensure_memory_mcp_config",
        lambda *a, **kw: StepResult("memory-mcp", StepStatus.OK, "ok"),
    )

    report = run_plan(valid_config, no_input=False, dry_run=False)
    by_name = {r.name: r for r in report.failed}
    assert "git" in by_name
    assert by_name["git"].status is StepStatus.FAIL
    assert "KeyError" in by_name["git"].detail


def test_run_plan_propagates_failed_and_needs_user_into_is_blocked(monkeypatch, valid_config):
    monkeypatch.setattr(
        "kagura_engineer.setup.git.ensure_git",
        lambda **kw: StepResult("git", StepStatus.OK, "ok"),
    )
    monkeypatch.setattr(
        "kagura_engineer.setup.claude.ensure_claude_login",
        lambda **kw: StepResult("claude-code", StepStatus.NEEDS_USER, "log in"),
    )
    monkeypatch.setattr(
        "kagura_engineer.setup.gh.ensure_gh_auth",
        lambda *a, **kw: StepResult("gh", StepStatus.OK, "ok"),
    )
    monkeypatch.setattr(
        "kagura_engineer.setup.ollama.ensure_ollama_up",
        lambda *a, **kw: StepResult("ollama", StepStatus.OK, "ok"),
    )
    monkeypatch.setattr(
        "kagura_engineer.setup.ollama.pull_ollama_models",
        lambda *a, **kw: StepResult("ollama-models", StepStatus.SKIPPED, "no models"),
    )
    monkeypatch.setattr(
        "kagura_engineer.setup.memory_cloud.ensure_memory_cloud_reachable",
        lambda *a, **kw: StepResult("memory-cloud", StepStatus.OK, "ok"),
    )
    monkeypatch.setattr(
        "kagura_engineer.setup.memory_mcp.ensure_memory_mcp_config",
        lambda *a, **kw: StepResult("memory-mcp", StepStatus.OK, "ok"),
    )

    report = run_plan(valid_config, no_input=False, dry_run=False)
    assert report.is_blocked is True
    assert any(r.name == "claude-code" for r in report.needs_user)


def test_run_plan_passes_platform_and_config_to_steps(monkeypatch, valid_config):
    """Each step's lambda wrapper is responsible for forwarding the
    right subset of (platform, config) — we just verify the call
    fires through the registry."""
    calls = []

    def _capture(name):
        def _f(*a, **kw):
            calls.append(name)
            return StepResult(name, StepStatus.OK, "ok")
        return _f

    monkeypatch.setattr("kagura_engineer.setup.git.ensure_git", _capture("git"))
    monkeypatch.setattr("kagura_engineer.setup.claude.ensure_claude_login", _capture("claude-code"))
    monkeypatch.setattr("kagura_engineer.setup.gh.ensure_gh_auth", _capture("gh"))
    monkeypatch.setattr("kagura_engineer.setup.ollama.ensure_ollama_up", _capture("ollama"))
    monkeypatch.setattr("kagura_engineer.setup.ollama.pull_ollama_models", _capture("ollama-models"))
    monkeypatch.setattr("kagura_engineer.setup.memory_cloud.ensure_memory_cloud_reachable", _capture("memory-cloud"))
    monkeypatch.setattr("kagura_engineer.setup.memory_mcp.ensure_memory_mcp_config", _capture("memory-mcp"))

    run_plan(valid_config, no_input=False, dry_run=False)
    assert calls == STEP_NAMES


def test_run_plan_threads_full_into_memory_mcp_step(monkeypatch, valid_config):
    # `--full` must reach the memory-mcp step (and only that step cares).
    seen = {}

    def _capture_mcp(cfg, *, no_input, dry_run, full=False, **kw):
        seen["full"] = full
        return StepResult("memory-mcp", StepStatus.OK, "ok")

    for dotted, nm in [
        ("kagura_engineer.setup.git.ensure_git", "git"),
        ("kagura_engineer.setup.claude.ensure_claude_login", "claude-code"),
        ("kagura_engineer.setup.gh.ensure_gh_auth", "gh"),
        ("kagura_engineer.setup.ollama.ensure_ollama_up", "ollama"),
        ("kagura_engineer.setup.ollama.pull_ollama_models", "ollama-models"),
        ("kagura_engineer.setup.memory_cloud.ensure_memory_cloud_reachable", "memory-cloud"),
    ]:
        monkeypatch.setattr(dotted, (lambda nm: lambda *a, **kw: StepResult(nm, StepStatus.OK, "ok"))(nm))
    monkeypatch.setattr(
        "kagura_engineer.setup.memory_mcp.ensure_memory_mcp_config", _capture_mcp
    )

    run_plan(valid_config, no_input=False, dry_run=False, full=True)
    assert seen["full"] is True
