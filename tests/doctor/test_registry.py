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
        "gh-issue-driven",
    }
    assert len(calls) == 8


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


def test_run_all_without_config_runs_only_config_free_checks(monkeypatch):
    # issue #71: doctor must degrade on a missing/invalid config — run_all(None)
    # runs the config-free subset (git, brain-cli, gh, haiku, gh-issue-driven)
    # and omits every config-dependent check (ollama, memory, memory-mcp).
    calls = []

    def _stub(name):
        def _c(*a, **k):
            calls.append(name)
            return CheckResult(name, Status.OK, "stub")

        return _c

    for fn_name, label in [
        ("check_git", "git"),
        ("check_claude_code", "claude-code"),
        ("check_gh", "gh"),
        ("check_haiku", "haiku"),
        ("check_gh_issue_driven", "gh-issue-driven"),
    ]:
        monkeypatch.setattr(registry.checks, fn_name, _stub(label))
    # These must NOT be invoked when there is no config.
    for fn_name in ("check_ollama", "check_memory_cloud", "check_local_memory", "check_memory_mcp"):
        monkeypatch.setattr(registry.checks, fn_name, _stub("SHOULD-NOT-RUN"))

    results = registry.run_all(None)
    names = {r.name for r in results}
    assert names == {"git", "claude-code", "gh", "haiku", "gh-issue-driven"}
    assert "SHOULD-NOT-RUN" not in calls


def test_run_all_without_config_defaults_brain_cli_to_claude(monkeypatch):
    # With no config we cannot know brain_backend; brain-CLI presence defaults
    # to the claude check (the Config default), never crashing on None.
    monkeypatch.setattr(registry.checks, "check_claude_code", lambda: CheckResult("claude-code", Status.OK, "ok"))
    monkeypatch.setattr(registry.checks, "check_codex", lambda: CheckResult("codex", Status.OK, "ok"))
    for fn_name in ("check_git", "check_gh", "check_haiku", "check_gh_issue_driven"):
        monkeypatch.setattr(registry.checks, fn_name, lambda *a, **k: CheckResult("x", Status.OK, "ok"))

    names = {r.name for r in registry.run_all(None)}
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
