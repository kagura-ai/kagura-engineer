import json as _json
import subprocess
import urllib.error

import pytest

from kagura_engineer.doctor import checks
from kagura_engineer.doctor.result import Status
from kagura_engineer.setup import auth as auth_module


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_git_ok_inside_repo(monkeypatch):
    monkeypatch.setattr(checks.shutil, "which", lambda _: "/usr/bin/git")
    monkeypatch.setattr(
        checks.subprocess, "run", lambda *a, **k: _completed(0, "true\n")
    )
    r = checks.check_git()
    assert r.status is Status.OK


def test_git_fail_when_missing(monkeypatch):
    monkeypatch.setattr(checks.shutil, "which", lambda _: None)
    r = checks.check_git()
    assert r.status is Status.FAIL
    assert "re-run doctor" in r.fix_hint


def test_claude_ok_with_api_key(monkeypatch):
    monkeypatch.setattr(checks.shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(
        checks.subprocess, "run", lambda *a, **k: _completed(0, "1.2.3\n")
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
    r = checks.check_claude_code()
    assert r.status is Status.OK
    assert "api_key" in r.detail


def test_claude_warn_subscription_unverified(monkeypatch):
    monkeypatch.setattr(checks.shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(
        checks.subprocess, "run", lambda *a, **k: _completed(0, "1.2.3\n")
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = checks.check_claude_code()
    assert r.status is Status.WARN
    assert "subscription" in r.detail


def test_claude_fail_when_missing(monkeypatch):
    monkeypatch.setattr(checks.shutil, "which", lambda _: None)
    r = checks.check_claude_code()
    assert r.status is Status.FAIL


def test_claude_fail_when_api_key_is_empty(monkeypatch):
    monkeypatch.setattr(checks.shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(
        checks.subprocess, "run", lambda *a, **k: _completed(0, "1.2.3\n")
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    r = checks.check_claude_code()
    assert r.status is Status.FAIL
    assert "empty" in r.detail.lower()


def test_claude_fail_on_nonzero_version(monkeypatch):
    monkeypatch.setattr(checks.shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(
        checks.subprocess, "run", lambda *a, **k: _completed(1, "", "boom")
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = checks.check_claude_code()
    assert r.status is Status.FAIL


def test_gh_ok_when_authed(monkeypatch):
    monkeypatch.setattr(checks.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(checks.subprocess, "run", lambda *a, **k: _completed(0))
    assert checks.check_gh().status is Status.OK


def test_gh_fail_when_not_authed(monkeypatch):
    monkeypatch.setattr(checks.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(
        checks.subprocess, "run", lambda *a, **k: _completed(1, "", "not logged in")
    )
    r = checks.check_gh()
    assert r.status is Status.FAIL
    assert "gh auth login" in r.fix_hint


class _FakeResp:
    def __init__(self, payload):
        self._p = _json.dumps(payload).encode()

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_ollama_ok_with_required_models(monkeypatch):
    payload = {
        "models": [{"name": "qwen2.5-coder:7b"}, {"name": "deepseek-coder:6.7b"}]
    }
    monkeypatch.setattr(
        checks.urllib.request, "urlopen", lambda *a, **k: _FakeResp(payload)
    )
    r = checks.check_ollama("http://localhost:11434", required=["qwen2.5-coder:7b"])
    assert r.status is Status.OK


def test_ollama_warn_when_model_missing(monkeypatch):
    payload = {"models": [{"name": "llama3:8b"}]}
    monkeypatch.setattr(
        checks.urllib.request, "urlopen", lambda *a, **k: _FakeResp(payload)
    )
    r = checks.check_ollama("http://localhost:11434", required=["qwen2.5-coder:7b"])
    assert r.status is Status.WARN
    assert "ollama pull" in r.fix_hint


def test_ollama_warn_on_non_dict_response(monkeypatch):
    monkeypatch.setattr(checks.urllib.request, "urlopen", lambda *a, **k: _FakeResp([]))
    r = checks.check_ollama("http://localhost:11434", required=["qwen2.5-coder:7b"])
    assert r.status is Status.WARN
    assert "unexpected" in r.detail.lower()


def test_ollama_ignores_non_dict_model_entries(monkeypatch):
    # models list contains a stray non-dict element alongside a valid one
    payload = {"models": ["corrupt-string-entry", {"name": "qwen2.5-coder:7b"}]}
    monkeypatch.setattr(
        checks.urllib.request, "urlopen", lambda *a, **k: _FakeResp(payload)
    )
    r = checks.check_ollama("http://localhost:11434", required=["qwen2.5-coder:7b"])
    assert (
        r.status is Status.OK
    )  # the valid model is still found; the stray entry must not crash


def test_ollama_untagged_config_matches_tagged_daemon_model(monkeypatch):
    payload = {"models": [{"name": "qwen2.5-coder:7b"}]}
    monkeypatch.setattr(
        checks.urllib.request, "urlopen", lambda *a, **k: _FakeResp(payload)
    )
    r = checks.check_ollama("http://localhost:11434", required=["qwen2.5-coder"])
    assert r.status is Status.OK


def test_ollama_tagged_config_matches_untagged_daemon_model(monkeypatch):
    # Daemon returned the model under its untagged default name (e.g. after
    # `ollama cp qwen2.5-coder:7b qwen2.5-coder`). The config still names
    # the tagged form. Matching must succeed.
    payload = {"models": [{"name": "qwen2.5-coder"}]}
    monkeypatch.setattr(
        checks.urllib.request, "urlopen", lambda *a, **k: _FakeResp(payload)
    )
    r = checks.check_ollama("http://localhost:11434", required=["qwen2.5-coder:7b"])
    assert r.status is Status.OK


def test_ollama_does_not_crash_when_model_entry_lacks_name(monkeypatch):
    # A dict entry without "name" (e.g. Ollama renaming the key to "model",
    # or a malformed/older endpoint) must NOT put None into `have` and then
    # crash _model_present via None.split(). It should degrade to WARN.
    payload = {"models": [{"model": "llama3:8b"}]}
    monkeypatch.setattr(
        checks.urllib.request, "urlopen", lambda *a, **k: _FakeResp(payload)
    )
    r = checks.check_ollama("http://localhost:11434", required=["llama3"])
    assert r.status is Status.WARN
    assert "llama3" in r.detail


def test_ollama_count_excludes_nameless_entries(monkeypatch):
    # A model dict missing "name" must not inflate the "N models available" count.
    payload = {"models": [{"name": "llama3"}, {"digest": "abc"}]}
    monkeypatch.setattr(
        checks.urllib.request, "urlopen", lambda *a, **k: _FakeResp(payload)
    )
    r = checks.check_ollama("http://localhost:11434", required=[])
    assert r.status is Status.OK
    assert "1 models available" in r.detail


def test_ollama_warn_when_models_field_is_null(monkeypatch):
    # Daemon (or a proxy) returns {"models": null}. Must not raise TypeError.
    payload = {"models": None}
    monkeypatch.setattr(
        checks.urllib.request, "urlopen", lambda *a, **k: _FakeResp(payload)
    )
    r = checks.check_ollama("http://localhost:11434", required=["qwen2.5-coder:7b"])
    # No required models present and an empty `have` set → WARN.
    assert r.status is Status.WARN
    assert "missing" in r.detail.lower()


def test_memory_cloud_ok_strips_credentials(monkeypatch):
    # If the configured URL embeds basic auth, the OK detail must not echo it.
    monkeypatch.setattr(
        checks.urllib.request, "urlopen", lambda *a, **k: _FakeResp({"status": "ok"})
    )
    r = checks.check_memory_cloud("https://svc:s3cret@memory.local")
    assert r.status is Status.OK
    assert "s3cret" not in r.detail
    assert "svc@" not in r.detail
    assert "memory.local" in r.detail


def test_ollama_fail_when_daemon_down(monkeypatch):
    def _boom(*a, **k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(checks.urllib.request, "urlopen", _boom)
    r = checks.check_ollama("http://localhost:11434", required=[])
    assert r.status is Status.FAIL
    assert "ollama serve" in r.fix_hint


def test_haiku_warn_without_auth(monkeypatch, tmp_path):
    # No env, no credential cache anywhere → WARN.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    fake_home = tmp_path  # empty dir, no .claude/ and no .claude.json
    monkeypatch.setattr(auth_module.Path, "home", classmethod(lambda cls: fake_home))
    r = checks.check_haiku()
    assert r.status is Status.WARN


def test_haiku_ok_with_auth(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
    assert checks.check_haiku().status is Status.OK


def test_haiku_fail_when_api_key_is_empty(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    r = checks.check_haiku()
    assert r.status is Status.FAIL
    assert "empty" in r.detail.lower()


def test_haiku_ok_with_subscription_credential_cache(monkeypatch, tmp_path):
    # No ANTHROPIC_API_KEY, but ~/.claude/.credentials.json exists → OK (subscription).
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    fake_home = tmp_path
    cred = fake_home / ".claude" / ".credentials.json"
    cred.parent.mkdir(parents=True)
    cred.write_text("{}")  # contents don't matter for P1
    monkeypatch.setattr(auth_module.Path, "home", classmethod(lambda cls: fake_home))
    r = checks.check_haiku()
    assert r.status is Status.OK
    assert "subscription" in r.detail.lower()


def test_haiku_ok_with_legacy_claude_json(monkeypatch, tmp_path):
    # Legacy fallback: ~/.claude.json exists, ~/.claude/.credentials.json does not.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    fake_home = tmp_path
    (fake_home / ".claude.json").write_text('{"oauthAccount": {"emailAddress": "x@example.com"}}')
    monkeypatch.setattr(auth_module.Path, "home", classmethod(lambda cls: fake_home))
    r = checks.check_haiku()
    assert r.status is Status.OK
    assert "subscription" in r.detail.lower()


def test_memory_cloud_ok_when_reachable(monkeypatch):
    monkeypatch.setattr(
        checks.urllib.request, "urlopen", lambda *a, **k: _FakeResp({"status": "ok"})
    )
    r = checks.check_memory_cloud("https://memory.kagura-ai.com")
    assert r.status is Status.OK


def test_memory_cloud_fail_when_unreachable(monkeypatch):
    def _boom(*a, **k):
        raise urllib.error.URLError("dns")

    monkeypatch.setattr(checks.urllib.request, "urlopen", _boom)
    r = checks.check_memory_cloud("https://memory.kagura-ai.com")
    assert r.status is Status.FAIL


def test_memory_cloud_ok_with_non_json_body(monkeypatch):
    class _PlainResp:
        def read(self):
            return b"OK"  # not JSON

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(checks.urllib.request, "urlopen", lambda *a, **k: _PlainResp())
    r = checks.check_memory_cloud("https://memory.kagura-ai.com")
    assert r.status is Status.OK


def test_memory_cloud_fail_on_malformed_url(monkeypatch):
    # A schemeless/garbage memory_cloud_url makes urlopen raise ValueError
    # ("unknown url type" / "Invalid IPv6 URL"), not URLError. It must be
    # caught and reported as FAIL — not crash the whole doctor command
    # (run_all has no per-check isolation).
    def _boom(*a, **k):
        raise ValueError("unknown url type: 'foo/health'")

    monkeypatch.setattr(checks.urllib.request, "urlopen", _boom)
    r = checks.check_memory_cloud("foo")
    assert r.status is Status.FAIL
    assert r.fix_hint is not None


def test_memory_cloud_warn_on_http_error(monkeypatch):
    def _boom(*a, **k):
        raise urllib.error.HTTPError(
            "https://memory.kagura-ai.com/health", 403, "Forbidden", {}, None
        )

    monkeypatch.setattr(checks.urllib.request, "urlopen", _boom)
    r = checks.check_memory_cloud("https://memory.kagura-ai.com")
    assert r.status is Status.WARN
    assert "403" in r.detail


def test_check_gh_issue_driven_ok_when_plugin_present(tmp_path, monkeypatch):
    from kagura_engineer.doctor import checks
    from kagura_engineer.doctor.result import Status

    plugins = tmp_path / "plugins"
    (plugins / "cache" / "gh-issue-driven" / "gh-issue-driven" / "0.13.0" / "commands").mkdir(parents=True)
    monkeypatch.setenv("KAGURA_PLUGINS_DIR", str(plugins))
    res = checks.check_gh_issue_driven()
    assert res.status is Status.OK


def test_check_gh_issue_driven_fail_when_absent(tmp_path, monkeypatch):
    from kagura_engineer.doctor import checks
    from kagura_engineer.doctor.result import Status

    monkeypatch.setenv("KAGURA_PLUGINS_DIR", str(tmp_path / "empty"))
    res = checks.check_gh_issue_driven()
    assert res.status is Status.FAIL
    assert res.is_blocking is True  # FAIL ⇒ blocking; run guard refuses to start


def test_check_local_memory_ok(tmp_path):
    from kagura_engineer.doctor import checks
    from kagura_engineer.doctor.result import Status
    r = checks.check_local_memory(str(tmp_path / "sub" / "mem.db"))
    assert r.status is Status.OK
    assert (tmp_path / "sub").is_dir()


def test_check_local_memory_fail_when_parent_unwritable(tmp_path):
    from kagura_engineer.doctor import checks
    from kagura_engineer.doctor.result import Status
    blocker = tmp_path / "afile"
    blocker.write_text("x")  # a file where a dir is expected → mkdir fails
    r = checks.check_local_memory(str(blocker / "nope" / "mem.db"))
    assert r.status is Status.FAIL
