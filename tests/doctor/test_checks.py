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


def test_memory_cloud_ok_strips_credentials(monkeypatch, tmp_path):
    # If the configured URL embeds basic auth, the OK detail must not echo it.
    monkeypatch.setattr(
        checks.urllib.request, "urlopen", lambda *a, **k: _FakeResp({"status": "ok"})
    )
    r = checks.check_memory_cloud(
        "https://svc:s3cret@memory.local", env={"KAGURA_API_KEY": "kg"}, home=tmp_path
    )
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


def test_memory_cloud_ok_when_reachable(monkeypatch, tmp_path):
    monkeypatch.setattr(
        checks.urllib.request, "urlopen", lambda *a, **k: _FakeResp({"status": "ok"})
    )
    r = checks.check_memory_cloud(
        "https://memory.kagura-ai.com", env={"KAGURA_API_KEY": "kg"}, home=tmp_path
    )
    assert r.status is Status.OK


def test_memory_cloud_fail_when_unreachable(monkeypatch):
    def _boom(*a, **k):
        raise urllib.error.URLError("dns")

    monkeypatch.setattr(checks.urllib.request, "urlopen", _boom)
    r = checks.check_memory_cloud("https://memory.kagura-ai.com")
    assert r.status is Status.FAIL


def test_memory_cloud_ok_with_non_json_body(monkeypatch, tmp_path):
    class _PlainResp:
        def read(self):
            return b"OK"  # not JSON

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(checks.urllib.request, "urlopen", lambda *a, **k: _PlainResp())
    r = checks.check_memory_cloud(
        "https://memory.kagura-ai.com", env={"KAGURA_API_KEY": "kg"}, home=tmp_path
    )
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


def test_memory_cloud_warn_on_http_error(monkeypatch, tmp_path):
    def _boom(*a, **k):
        raise urllib.error.HTTPError(
            "https://memory.kagura-ai.com/health", 403, "Forbidden", {}, None
        )

    monkeypatch.setattr(checks.urllib.request, "urlopen", _boom)
    r = checks.check_memory_cloud(
        "https://memory.kagura-ai.com", env={"KAGURA_API_KEY": "kg"}, home=tmp_path
    )
    assert r.status is Status.WARN
    assert "403" in r.detail


def test_memory_cloud_warn_when_reachable_but_no_credential(monkeypatch, tmp_path):
    # Reachable host + NO credential resolves (no KAGURA_API_KEY, no
    # `kagura auth login` cache) must NOT be a silent pass — it is the exact
    # first-run footgun from issue #6: doctor passes, run dies. WARN + guide.
    monkeypatch.setattr(
        checks.urllib.request, "urlopen", lambda *a, **k: _FakeResp({"status": "ok"})
    )
    r = checks.check_memory_cloud(
        "https://memory.kagura-ai.com", env={}, home=tmp_path
    )
    assert r.status is Status.WARN
    assert "credential" in r.detail.lower()
    # The hint must name both real fixes (env var and login).
    assert "KAGURA_API_KEY" in r.fix_hint
    assert "kagura auth login" in r.fix_hint


def test_memory_cloud_ok_reports_env_api_key_auth(monkeypatch, tmp_path):
    monkeypatch.setattr(
        checks.urllib.request, "urlopen", lambda *a, **k: _FakeResp({"status": "ok"})
    )
    r = checks.check_memory_cloud(
        "https://memory.kagura-ai.com", env={"KAGURA_API_KEY": "kg"}, home=tmp_path
    )
    assert r.status is Status.OK
    assert "auth" in r.detail.lower()


def test_memory_cloud_ok_reports_oauth_profile_auth(monkeypatch, tmp_path):
    # `kagura auth login` cache present, no env key → OK via the OAuth profile.
    import json as _j

    cred = tmp_path / ".kagura" / "credentials.json"
    cred.parent.mkdir(parents=True)
    # SDK loader requires the full credential shape (issue #36).
    _full = {
        "server": "https://memory.kagura-ai.com",
        "mcp_url": "https://memory.kagura-ai.com/mcp",
        "client_id": "cid",
        "access_token": "tok",
        "refresh_token": "rtok",
        "token_type": "Bearer",
        "expires_at": "2099-01-01T00:00:00+00:00",
    }
    cred.write_text(_j.dumps({"default_profile": "default", "profiles": {"default": _full}}))
    monkeypatch.setattr(
        checks.urllib.request, "urlopen", lambda *a, **k: _FakeResp({"status": "ok"})
    )
    r = checks.check_memory_cloud(
        "https://memory.kagura-ai.com", env={}, home=tmp_path
    )
    assert r.status is Status.OK
    assert "auth" in r.detail.lower()


def test_memory_cloud_http_error_without_credential_guides_auth(monkeypatch, tmp_path):
    # 401/403 with no credential is the auth smoking gun: the hint must point
    # at the credential, not just defer to a later smoke.
    def _boom(*a, **k):
        raise urllib.error.HTTPError(
            "https://memory.kagura-ai.com/health", 401, "Unauthorized", {}, None
        )

    monkeypatch.setattr(checks.urllib.request, "urlopen", _boom)
    r = checks.check_memory_cloud(
        "https://memory.kagura-ai.com", env={}, home=tmp_path
    )
    assert r.status is Status.WARN
    assert "KAGURA_API_KEY" in r.fix_hint


def test_check_codex_fails_when_absent(monkeypatch):
    monkeypatch.setattr(checks.shutil, "which", lambda name: None)
    res = checks.check_codex()
    assert res.name == "codex"
    assert res.status is Status.FAIL


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


# --- check_memory_mcp (issue #36) --------------------------------------


def _login(home, profile="default"):
    import json as _json
    # The resolver delegates to the SDK loader (issue #36): a profile only
    # counts when it carries the full OAuth credential shape.
    full = {
        "server": "https://memory.kagura-ai.com",
        "mcp_url": "https://memory.kagura-ai.com/mcp",
        "client_id": "cid",
        "access_token": "t",
        "refresh_token": "r",
        "token_type": "Bearer",
        "expires_at": "2099-01-01T00:00:00+00:00",
    }
    cred = home / ".kagura" / "credentials.json"
    cred.parent.mkdir(parents=True, exist_ok=True)
    cred.write_text(_json.dumps({"default_profile": profile, "profiles": {profile: full}}))


def test_check_memory_mcp_warns_when_absent(tmp_path):
    from kagura_engineer.doctor import checks
    from kagura_engineer.doctor.result import Status
    r = checks.check_memory_mcp(tmp_path, env={"KAGURA_API_KEY": "kg"}, home=tmp_path)
    assert r.status is Status.WARN
    assert "setup" in (r.fix_hint or "").lower()
    assert r.is_blocking is False  # WARN is advisory, not a run-blocker


def test_check_memory_mcp_ok_for_stdio_with_credential(tmp_path):
    from kagura_engineer.doctor import checks
    from kagura_engineer.doctor.result import Status
    from kagura_memory.setup_claude import _write_mcp_json_stdio
    _login(tmp_path, profile="default")
    _write_mcp_json_stdio(tmp_path, "default")
    r = checks.check_memory_mcp(tmp_path, env={}, home=tmp_path)
    assert r.status is Status.OK
    assert "stdio" in r.detail.lower()


def test_check_memory_mcp_ok_for_static_token(tmp_path):
    from kagura_engineer.doctor import checks
    from kagura_engineer.doctor.result import Status
    from kagura_memory.setup_claude import _write_mcp_json
    _write_mcp_json(tmp_path, "kg-secret", "https://memory.kagura-ai.com/mcp")
    r = checks.check_memory_mcp(tmp_path, env={"KAGURA_API_KEY": "kg-secret"}, home=tmp_path)
    assert r.status is Status.OK


def test_check_memory_mcp_warns_when_config_present_but_no_credential(tmp_path):
    # A generated stdio config whose profile credential has since gone away
    # is stale — the proxy will 401. Flag it (WARN), don't pass silently.
    from kagura_engineer.doctor import checks
    from kagura_engineer.doctor.result import Status
    from kagura_memory.setup_claude import _write_mcp_json_stdio
    _write_mcp_json_stdio(tmp_path, "default")
    r = checks.check_memory_mcp(tmp_path, env={}, home=tmp_path)
    assert r.status is Status.WARN
    assert "credential" in r.detail.lower() or "credential" in (r.fix_hint or "").lower()
