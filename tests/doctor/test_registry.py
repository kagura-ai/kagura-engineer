from kagura_engineer.doctor import registry
from kagura_engineer.doctor.result import CheckResult, Status


def test_run_all_invokes_every_check(monkeypatch, valid_config):
    calls = []

    def _stub(name):
        def _c(*a, **k):
            calls.append(name)
            return CheckResult(name, Status.OK, "stub")

        return _c

    monkeypatch.setattr(registry.checks, "check_git", _stub("git"))
    monkeypatch.setattr(registry.checks, "check_claude_code", _stub("claude-code"))
    monkeypatch.setattr(registry.checks, "check_gh", _stub("gh"))
    monkeypatch.setattr(registry.checks, "check_ollama", _stub("ollama"))
    monkeypatch.setattr(registry.checks, "check_haiku", _stub("haiku"))
    monkeypatch.setattr(registry.checks, "check_memory_cloud", _stub("memory-cloud"))
    monkeypatch.setattr(registry.checks, "check_memory_mcp", _stub("memory-mcp"))
    monkeypatch.setattr(registry.checks, "check_memory_context", _stub("memory-context"))
    monkeypatch.setattr(registry.checks, "check_gh_issue_driven", _stub("gh-issue-driven"))

    results = registry.run_all(valid_config)
    assert {r.name for r in results} == {
        "git",
        "claude-code",
        "gh",
        "ollama",
        "haiku",
        "memory-cloud",
        "memory-mcp",
        "memory-context",
        "gh-issue-driven",
    }
    assert len(calls) == 9


def test_overall_status_is_worst():
    results = [
        CheckResult("a", Status.OK, ""),
        CheckResult("b", Status.WARN, ""),
        CheckResult("c", Status.FAIL, ""),
    ]
    assert registry.overall_status(results) is Status.FAIL


def test_overall_status_warn_when_no_fail():
    results = [CheckResult("a", Status.OK, ""), CheckResult("b", Status.WARN, "")]
    assert registry.overall_status(results) is Status.WARN


def test_overall_status_empty_is_ok():
    assert registry.overall_status([]) is Status.OK


def test_run_all_isolates_check_exceptions(monkeypatch, valid_config):
    # A buggy check must not abort the rest of the doctor run.
    def _boom(*a, **k):
        raise KeyError("malformed response")

    monkeypatch.setattr(registry.checks, "check_git", _boom)
    monkeypatch.setattr(
        registry.checks,
        "check_claude_code",
        lambda: CheckResult("claude-code", Status.OK, "ok"),
    )
    monkeypatch.setattr(
        registry.checks,
        "check_gh",
        lambda: CheckResult("gh", Status.OK, "ok"),
    )
    monkeypatch.setattr(
        registry.checks,
        "check_ollama",
        lambda *a, **k: CheckResult("ollama", Status.OK, "ok"),
    )
    monkeypatch.setattr(
        registry.checks,
        "check_haiku",
        lambda: CheckResult("haiku", Status.OK, "ok"),
    )
    monkeypatch.setattr(
        registry.checks,
        "check_memory_cloud",
        lambda *a, **k: CheckResult("memory-cloud", Status.OK, "ok"),
    )
    monkeypatch.setattr(
        registry.checks,
        "check_memory_mcp",
        lambda *a, **k: CheckResult("memory-mcp", Status.OK, "ok"),
    )
    monkeypatch.setattr(
        registry.checks,
        "check_memory_context",
        lambda *a, **k: CheckResult("memory-context", Status.OK, "ok"),
    )
    monkeypatch.setattr(
        registry.checks,
        "check_gh_issue_driven",
        lambda: CheckResult("gh-issue-driven", Status.OK, "ok"),
    )

    results = registry.run_all(valid_config)
    by_name = {r.name: r for r in results}
    # The failing check surfaces as a FAIL row, not a propagated exception.
    assert by_name["git"].status is Status.FAIL
    assert "KeyError" in by_name["git"].detail
    # All other checks still ran.
    assert {r.name for r in results} == {
        "git",
        "claude-code",
        "gh",
        "ollama",
        "haiku",
        "memory-cloud",
        "memory-mcp",
        "memory-context",
        "gh-issue-driven",
    }
    assert all(
        by_name[n].status is Status.OK for n in by_name if n != "git"
    )


def test_registry_checks_codex_when_backend_is_codex(valid_config):
    cfg = valid_config.model_copy(update={"brain_backend": "codex"})
    names = {c.name for c in registry.run_all(cfg)}
    assert "codex" in names
    assert "claude-code" not in names


def test_registry_checks_claude_when_backend_is_claude(valid_config):
    cfg = valid_config.model_copy(update={"brain_backend": "claude"})
    names = {c.name for c in registry.run_all(cfg)}
    assert "claude-code" in names
    assert "codex" not in names


def test_run_all_uses_local_memory_check_when_backend_local(monkeypatch, valid_config):
    local_cfg = valid_config.model_copy(update={"memory_backend": "local"})
    called = {"local": 0, "cloud": 0}

    def _local(*a, **k):
        called["local"] += 1
        return CheckResult("memory-local", Status.OK, "ok")

    def _cloud(*a, **k):
        called["cloud"] += 1
        return CheckResult("memory-cloud", Status.OK, "ok")

    monkeypatch.setattr(registry.checks, "check_local_memory", _local)
    monkeypatch.setattr(registry.checks, "check_memory_cloud", _cloud)
    for name in ("check_git", "check_claude_code", "check_gh", "check_ollama",
                 "check_haiku", "check_gh_issue_driven"):
        monkeypatch.setattr(registry.checks, name, lambda *a, **k: CheckResult("x", Status.OK, "ok"))

    results = registry.run_all(local_cfg)
    assert called["local"] == 1 and called["cloud"] == 0
    assert any(r.name == "memory-local" for r in results)
    # The MCP-config check is cloud-only: the offline SQLite backend has no
    # MCP memory server, so no memory-mcp row appears for a local repo.
    assert not any(r.name == "memory-mcp" for r in results)
    # Same for the context-resolution check (issue #70): a local backend has
    # no cloud context to resolve, so the check is skipped entirely.
    assert not any(r.name == "memory-context" for r in results)


def test_memory_context_check_is_in_cloud_plan(monkeypatch, valid_config):
    # issue #70: the wrong-context detector runs for the cloud backend.
    monkeypatch.setattr(
        registry.checks,
        "check_memory_context",
        lambda *a, **k: CheckResult("memory-context", Status.OK, "ok"),
    )
    for name in ("check_git", "check_claude_code", "check_gh", "check_ollama",
                 "check_haiku", "check_memory_cloud", "check_memory_mcp",
                 "check_gh_issue_driven"):
        monkeypatch.setattr(registry.checks, name,
                            lambda *a, **k: CheckResult("x", Status.OK, "ok"))
    results = registry.run_all(valid_config)
    assert any(r.name == "memory-context" for r in results)
