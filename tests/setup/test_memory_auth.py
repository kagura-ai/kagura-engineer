"""Unit tests for the Memory Cloud auth resolver.

Mirrors `resolve_anthropic_auth` (tests/setup/test_auth.py): a pure
function over (env, home) that decides which Memory Cloud credential is
in effect — the env API key (`KAGURA_API_KEY`) or the `kagura auth login`
OAuth profile cache (`~/.kagura/credentials.json`). The orchestrator-level
side effects (running `kagura auth login`) live elsewhere; this resolver
only finds, it does not act.
"""
from __future__ import annotations

import os
from pathlib import Path

from kagura_engineer.setup.memory_auth import (
    MemoryAuthMethod,
    resolve_memory_cloud_auth,
)


def _full_profile(**over) -> dict:
    """A complete OAuth credential, as `kagura auth login` would write it.

    The resolver delegates to the SDK loader (issue #36), which requires the
    full credential shape — a partial stub no longer counts as a working login
    (that is the point: a half-written file would 401 at run time).
    """
    p = {
        "server": "https://memory.kagura-ai.com",
        "mcp_url": "https://memory.kagura-ai.com/mcp",
        "client_id": "cid",
        "access_token": "tok",
        "refresh_token": "rtok",
        "token_type": "Bearer",
        "expires_at": "2099-01-01T00:00:00+00:00",
        "scope": "",
    }
    p.update(over)
    return p


def _write_credentials(home: Path, profiles: dict, default_profile: str = "default") -> Path:
    """Write a `~/.kagura/credentials.json` with the given profiles, filling
    each profile out to the full credential shape the SDK loader requires."""
    import json

    full = {name: _full_profile(**(val or {})) for name, val in profiles.items()}
    cred = home / ".kagura" / "credentials.json"
    cred.parent.mkdir(parents=True, exist_ok=True)
    cred.write_text(
        json.dumps(
            {"version": 1, "default_profile": default_profile, "profiles": full}
        )
    )
    return cred


# --- env var branch -----------------------------------------------------


def test_env_key_wins_when_set_to_nonempty(tmp_path):
    # Even when a login profile exists, env wins (documented precedence).
    _write_credentials(tmp_path, {"default": {"access_token": "tok"}})
    res = resolve_memory_cloud_auth(
        env={"KAGURA_API_KEY": "kg-fake"}, home=tmp_path
    )
    assert res.method is MemoryAuthMethod.ENV_API_KEY


def test_env_key_empty_string_treated_as_unset(tmp_path):
    # Empty string is a deliberate unset signal: fall through to the cache.
    res = resolve_memory_cloud_auth(env={"KAGURA_API_KEY": ""}, home=tmp_path)
    assert res.method is MemoryAuthMethod.NONE


def test_env_key_whitespace_only_treated_as_unset(tmp_path):
    # A whitespace-only key (e.g. a stray-space `.env` line) would make the
    # SDK send `Authorization: Bearer ` and 401, so `_resolve_auth` strips
    # and falls through. The resolver must match — reporting it as present
    # would re-create the "doctor passes, run dies" mismatch.
    res = resolve_memory_cloud_auth(env={"KAGURA_API_KEY": "   "}, home=tmp_path)
    assert res.method is MemoryAuthMethod.NONE


# --- OAuth profile cache branch ----------------------------------------


def test_oauth_profile_detected_from_credentials_cache(tmp_path):
    _write_credentials(tmp_path, {"default": {"access_token": "tok"}})
    res = resolve_memory_cloud_auth(env={}, home=tmp_path)
    assert res.method is MemoryAuthMethod.OAUTH_PROFILE
    assert res.profile == "default"
    assert "kagura auth login" in res.detail.lower() or "default" in res.detail


def test_oauth_profile_honors_default_profile_name(tmp_path):
    _write_credentials(
        tmp_path, {"work": {"access_token": "tok"}}, default_profile="work"
    )
    res = resolve_memory_cloud_auth(env={}, home=tmp_path)
    assert res.method is MemoryAuthMethod.OAUTH_PROFILE
    assert res.profile == "work"


def test_default_profile_pointing_at_missing_entry_is_not_a_login(tmp_path):
    # default_profile names a profile that does not exist in `profiles`.
    # The SDK's `get_profile(None)` is `profiles.get(default_profile)`, which
    # returns None here — it never falls back to an arbitrary profile. The
    # resolver must report NONE too; guessing a profile the SDK would not
    # select re-creates the footgun this module exists to close.
    _write_credentials(
        tmp_path, {"work": {"access_token": "tok"}}, default_profile="stale"
    )
    res = resolve_memory_cloud_auth(env={}, home=tmp_path)
    assert res.method is MemoryAuthMethod.NONE


def test_empty_profiles_dict_is_not_a_login(tmp_path):
    _write_credentials(tmp_path, {})
    res = resolve_memory_cloud_auth(env={}, home=tmp_path)
    assert res.method is MemoryAuthMethod.NONE


def test_malformed_credentials_file_is_not_a_login(tmp_path):
    cred = tmp_path / ".kagura" / "credentials.json"
    cred.parent.mkdir(parents=True, exist_ok=True)
    cred.write_text("not json {{{")
    res = resolve_memory_cloud_auth(env={}, home=tmp_path)
    assert res.method is MemoryAuthMethod.NONE


# --- none branch --------------------------------------------------------


def test_no_auth_source_returns_none(tmp_path):
    res = resolve_memory_cloud_auth(env={}, home=tmp_path)
    assert res.method is MemoryAuthMethod.NONE
    assert res.detail == ""
    assert res.profile is None


def test_defaults_to_os_environ_and_home(monkeypatch, tmp_path):
    # No injected env/home → reads os.environ and Path.home().
    monkeypatch.setattr(os, "environ", {"KAGURA_API_KEY": "kg-fake"})
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    res = resolve_memory_cloud_auth()
    assert res.method is MemoryAuthMethod.ENV_API_KEY
