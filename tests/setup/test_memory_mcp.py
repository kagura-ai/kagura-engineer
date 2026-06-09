"""Unit tests for the setup.memory_mcp step (issue #36).

`ensure_memory_mcp_config` generates `<repo>/.mcp.json` so headless
`claude -p` can reach the kagura-memory MCP tools — no hand-authoring.

Two write modes:

  * OAuth profile present  -> stdio form (`kagura-mcp --profile <p>`); the
    refresh-aware proxy injects a fresh token per request, no secret baked in.
  * `KAGURA_API_KEY` only  -> legacy static-token url form (CI / service acct).

Scope: default is `.mcp.json`-only. `--full` additionally installs the SDK's
hooks + skills via `run_setup_claude`; the default must NOT touch `.claude/`.
"""
from __future__ import annotations

import json

from kagura_engineer.config import Config
from kagura_engineer.setup import memory_mcp
from kagura_engineer.setup.memory_mcp import ensure_memory_mcp_config
from kagura_engineer.setup.result import StepStatus


def _cloud_cfg(**over) -> Config:
    base = dict(
        profile="dev",
        memory_cloud_url="https://memory.kagura-ai.com",
        workspace_id="ws",
        context_id="ctx",
    )
    base.update(over)
    return Config(**base)


def _full_profile():
    # The SDK loader (issue #36) requires the full credential shape.
    return {
        "server": "https://memory.kagura-ai.com",
        "mcp_url": "https://memory.kagura-ai.com/mcp",
        "client_id": "cid",
        "access_token": "tok",
        "refresh_token": "rtok",
        "token_type": "Bearer",
        "expires_at": "2099-01-01T00:00:00+00:00",
    }


def _write_login(home, profiles=None, default_profile="default"):
    profiles = profiles if profiles is not None else {"default": {}}
    full = {name: _full_profile() for name in profiles}
    cred = home / ".kagura" / "credentials.json"
    cred.parent.mkdir(parents=True, exist_ok=True)
    cred.write_text(json.dumps({"default_profile": default_profile, "profiles": full}))


def test_local_backend_is_skipped(tmp_path):
    cfg = _cloud_cfg(memory_backend="local")
    r = ensure_memory_mcp_config(
        cfg, no_input=False, dry_run=False, repo_dir=tmp_path, env={}, home=tmp_path
    )
    assert r.status is StepStatus.SKIPPED
    assert not (tmp_path / ".mcp.json").exists()


def test_needs_user_when_no_credential(tmp_path):
    cfg = _cloud_cfg()
    r = ensure_memory_mcp_config(
        cfg, no_input=False, dry_run=False, repo_dir=tmp_path, env={}, home=tmp_path
    )
    assert r.status is StepStatus.NEEDS_USER
    assert "KAGURA_API_KEY" in r.fix_hint
    assert "kagura auth login" in r.fix_hint
    assert not (tmp_path / ".mcp.json").exists()


def test_dry_run_does_not_write(tmp_path):
    _write_login(tmp_path)
    cfg = _cloud_cfg()
    r = ensure_memory_mcp_config(
        cfg, no_input=False, dry_run=True, repo_dir=tmp_path, env={}, home=tmp_path
    )
    assert r.status is StepStatus.OK
    assert "would" in r.detail.lower()
    assert not (tmp_path / ".mcp.json").exists()


def test_oauth_profile_writes_stdio_form(tmp_path):
    _write_login(tmp_path, default_profile="work", profiles={"work": {"access_token": "t"}})
    cfg = _cloud_cfg()
    r = ensure_memory_mcp_config(
        cfg, no_input=False, dry_run=False, repo_dir=tmp_path, env={}, home=tmp_path
    )
    assert r.status is StepStatus.OK
    mcp = json.loads((tmp_path / ".mcp.json").read_text())
    entry = mcp["mcpServers"]["kagura-memory"]
    assert entry["type"] == "stdio"
    assert entry["command"] == "kagura-mcp"
    assert entry["args"] == ["--profile", "work"]
    # stdio form bakes no secret
    assert "headers" not in entry
    assert "stdio" in r.detail.lower()


def test_env_key_writes_static_token_form(tmp_path):
    cfg = _cloud_cfg()
    r = ensure_memory_mcp_config(
        cfg,
        no_input=False,
        dry_run=False,
        repo_dir=tmp_path,
        env={"KAGURA_API_KEY": "kg-secret"},
        home=tmp_path,
    )
    assert r.status is StepStatus.OK
    entry = json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]["kagura-memory"]
    assert entry["type"] == "url"
    assert entry["headers"]["Authorization"] == "Bearer kg-secret"


def test_default_does_not_install_hooks_or_skills(tmp_path):
    _write_login(tmp_path)
    cfg = _cloud_cfg()
    ensure_memory_mcp_config(
        cfg, no_input=False, dry_run=False, repo_dir=tmp_path, env={}, home=tmp_path
    )
    # mcp-only: no .claude/ hooks or command skills committed into the repo
    assert not (tmp_path / ".claude").exists()


def test_full_installs_via_run_setup_claude(tmp_path, monkeypatch):
    _write_login(tmp_path, default_profile="work", profiles={"work": {"access_token": "t"}})
    calls = {}

    def _fake_run_setup_claude(api_key, mcp_url, context_id, project_dir, non_interactive, **kw):
        calls.update(
            api_key=api_key,
            context_id=context_id,
            project_dir=project_dir,
            non_interactive=non_interactive,
            profile=kw.get("profile"),
        )
        # the real SDK writes .mcp.json; emulate so the step can report it
        (tmp_path / ".mcp.json").write_text("{}")

    monkeypatch.setattr(memory_mcp, "run_setup_claude", _fake_run_setup_claude)
    cfg = _cloud_cfg()
    r = ensure_memory_mcp_config(
        cfg, no_input=False, dry_run=False, full=True, repo_dir=tmp_path, env={}, home=tmp_path
    )
    assert r.status is StepStatus.OK
    assert "full" in r.detail.lower()
    assert calls["profile"] == "work"
    assert calls["non_interactive"] is True
    assert calls["context_id"] == "ctx"
