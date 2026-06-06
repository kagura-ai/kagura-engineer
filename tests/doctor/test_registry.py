from kagura_engineer.config import Config
from kagura_engineer.doctor import registry
from kagura_engineer.doctor.result import CheckResult, Status


def _cfg():
    return Config(
        profile="coding",
        memory_cloud_url="https://memory.kagura-ai.com",
        context_id="550e8400-e29b-41d4-a716-446655440000",
        review={"models": ["qwen2.5-coder:7b"]},
    )


def test_run_all_invokes_every_check(monkeypatch):
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

    results = registry.run_all(_cfg())
    assert {r.name for r in results} == {
        "git",
        "claude-code",
        "gh",
        "ollama",
        "haiku",
        "memory-cloud",
    }
    assert len(calls) == 6


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


def test_run_all_isolates_check_exceptions(monkeypatch):
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

    results = registry.run_all(_cfg())
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
    }
    assert all(
        by_name[n].status is Status.OK for n in by_name if n != "git"
    )
