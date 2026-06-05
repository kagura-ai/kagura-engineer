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
