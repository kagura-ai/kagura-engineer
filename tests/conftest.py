"""Shared pytest fixtures for kagura-engineer tests.

The canonical `Config` shape and minimal valid `repo.yaml` body are
defined in `_constants.py`; this file turns them into pytest fixtures
and provides a `Config` instance directly.
"""
from __future__ import annotations

import os

import pytest

from kagura_engineer.config import Config
from tests._constants import (
    VALID_CONTEXT_UUID,
    VALID_MEMORY_URL,
    VALID_PROFILE,
    VALID_WORKSPACE,
)


@pytest.fixture
def valid_repo_yaml_text() -> str:
    """The body of a minimal but valid repo.yaml."""
    return (
        f"profile: {VALID_PROFILE}\n"
        f"memory_cloud_url: {VALID_MEMORY_URL}\n"
        f"workspace_id: {VALID_WORKSPACE}\n"
        f"context_id: {VALID_CONTEXT_UUID}\n"
    )


@pytest.fixture
def write_cfg(tmp_path, valid_repo_yaml_text):
    """Write a valid repo.yaml into tmp_path and return its Path."""
    p = tmp_path / "repo.yaml"
    p.write_text(valid_repo_yaml_text)
    return p


@pytest.fixture
def valid_config() -> Config:
    """An in-memory valid `Config` instance."""
    return Config(
        profile=VALID_PROFILE,
        memory_cloud_url=VALID_MEMORY_URL,
        workspace_id=VALID_WORKSPACE,
        context_id=VALID_CONTEXT_UUID,
    )


@pytest.fixture
def permissive_umask():
    """Force the classic permissive umask (022) so the perms tests fail unless
    the code sets modes explicitly — not because the host umask happens to be
    strict already."""
    old = os.umask(0o022)
    yield
    os.umask(old)
