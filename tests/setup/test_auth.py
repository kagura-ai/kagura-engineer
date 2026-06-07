"""Unit tests for the shared Anthropic auth resolver.

Used by both doctor (check_haiku) and setup (ensure_claude_login).
The resolver is a pure function over (env, Path.home()); the
orchestrator-level side effects (writing to the credential cache,
running `claude` interactively) live in setup.claude, not here.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from kagura_engineer.setup.auth import (
    AuthMethod,
    resolve_anthropic_auth,
)


# --- env var branch -----------------------------------------------------


def test_env_key_wins_when_set_to_nonempty(monkeypatch, tmp_path):
    monkeypatch.setattr(os, "environ", {"ANTHROPIC_API_KEY": "sk-ant-fake"})
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    # Even when a credential cache exists, env wins.
    (tmp_path / ".claude" / ".credentials.json").parent.mkdir(parents=True)
    (tmp_path / ".claude" / ".credentials.json").write_text("{}")
    res = resolve_anthropic_auth()
    assert res.method is AuthMethod.ENV_API_KEY
    assert res.detail == "sk-ant-fake"  # not redacted; surface for debug


def test_env_key_empty_string_treated_as_unset(monkeypatch, tmp_path):
    # Empty string is a deliberate unset signal (matches doctor behavior
    # from commit c29f939 — Plan 1 surfaces this as a config FAIL).
    # The resolver itself does not FAIL; it just falls through to the
    # next source. The caller decides whether empty-string is a hard
    # error.
    monkeypatch.setattr(os, "environ", {"ANTHROPIC_API_KEY": ""})
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    res = resolve_anthropic_auth()
    assert res.method is AuthMethod.NONE


# --- credential cache branch --------------------------------------------


def test_subscription_cache_modern(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cache = tmp_path / ".claude" / ".credentials.json"
    cache.parent.mkdir(parents=True)
    cache.write_text("{}")
    # Pin mtime so the age is deterministic.
    mtime = time.time() - 86400 * 3  # 3 days old
    os.utime(cache, (mtime, mtime))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    res = resolve_anthropic_auth()
    assert res.method is AuthMethod.SUBSCRIPTION_CACHE
    assert "modern" in res.detail or "credentials.json" in res.detail
    assert res.cache_path == cache


def test_subscription_cache_legacy_when_modern_absent(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    legacy = tmp_path / ".claude.json"
    legacy.write_text("{}")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    res = resolve_anthropic_auth()
    assert res.method is AuthMethod.SUBSCRIPTION_CACHE
    assert res.cache_path == legacy
    assert "legacy" in res.detail


def test_modern_cache_wins_over_legacy(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    modern = tmp_path / ".claude" / ".credentials.json"
    modern.parent.mkdir(parents=True)
    modern.write_text("{}")
    legacy = tmp_path / ".claude.json"
    legacy.write_text("{}")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    res = resolve_anthropic_auth()
    assert res.cache_path == modern


# --- none branch --------------------------------------------------------


def test_no_auth_source_returns_none(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    res = resolve_anthropic_auth()
    assert res.method is AuthMethod.NONE
    assert res.detail == ""
    assert res.cache_path is None


def test_partial_cache_file_is_ignored(monkeypatch, tmp_path):
    # An empty or unreadable cache file should not be treated as
    # a successful subscription login. We treat presence + non-empty
    # bytes as the signal.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cache = tmp_path / ".claude" / ".credentials.json"
    cache.parent.mkdir(parents=True)
    cache.write_text("")  # zero-byte
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    res = resolve_anthropic_auth()
    assert res.method is AuthMethod.NONE
