from pathlib import Path

import pytest

from kagura_engineer.config import Config, load_config
from kagura_engineer.config import ConfigError

from tests._constants import (
    VALID_CONTEXT_UUID,
    VALID_MEMORY_URL,
    VALID_PROFILE,
    VALID_WORKSPACE,
)


def test_load_minimal_yaml(write_cfg):
    cfg = load_config(write_cfg)
    assert cfg.profile == VALID_PROFILE
    assert cfg.memory_cloud_url == VALID_MEMORY_URL
    assert cfg.workspace_id == VALID_WORKSPACE
    assert cfg.context_id == VALID_CONTEXT_UUID
    assert cfg.review.models == []
    assert cfg.review.max_loops == 3
    assert cfg.ollama_url == "http://localhost:11434"


def test_review_models_override(tmp_path, valid_repo_yaml_text):
    p = tmp_path / "repo.yaml"
    p.write_text(
        valid_repo_yaml_text
        + "review:\n"
        "  models: [qwen2.5-coder:7b, haiku]\n"
        "  max_loops: 5\n"
    )
    cfg = load_config(p)
    assert cfg.review.models == ["qwen2.5-coder:7b", "haiku"]
    assert cfg.review.max_loops == 5


def test_workspace_id_is_required(tmp_path, valid_repo_yaml_text):
    # workspace_id is the only required field not present in the canonical
    # body — strip it out and confirm the loader rejects the result.
    body = "\n".join(
        line for line in valid_repo_yaml_text.splitlines() if "workspace_id" not in line
    )
    p = tmp_path / "repo.yaml"
    p.write_text(body)
    with pytest.raises(ConfigError):
        load_config(p)


def test_context_id_is_required(tmp_path, valid_repo_yaml_text):
    body = "\n".join(
        line for line in valid_repo_yaml_text.splitlines() if "context_id" not in line
    )
    p = tmp_path / "repo.yaml"
    p.write_text(body)
    with pytest.raises(ConfigError):
        load_config(p)


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.yaml")


def test_unreadable_file_raises_config_error(tmp_path, monkeypatch, valid_repo_yaml_text):
    # File exists (is_file() True) but read_text() raises PermissionError
    # (mode 000 / owned by another user). The loader's docstring promises
    # ConfigError for an unreadable config; a raw OSError would escape the
    # CLI's `except ConfigError` and crash instead of exiting 2.
    p = tmp_path / "repo.yaml"
    p.write_text(valid_repo_yaml_text)

    def _boom(self, *a, **k):
        raise PermissionError("Permission denied")

    monkeypatch.setattr(Path, "read_text", _boom)
    with pytest.raises(ConfigError):
        load_config(p)


def test_malformed_yaml_raises_config_error(tmp_path):
    p = tmp_path / "repo.yaml"
    p.write_text("profile: coding\n\tbad: tab\n")  # tab → YAML scanner error
    with pytest.raises(ConfigError):
        load_config(p)


def test_valid_config_fixture_is_well_formed(valid_config):
    # The fixture itself is the contract; if a future Config field is added
    # without updating conftest, this assertion fires before any other test
    # that depends on valid_config.
    assert valid_config.profile == VALID_PROFILE
    assert valid_config.workspace_id == VALID_WORKSPACE
    assert valid_config.context_id == VALID_CONTEXT_UUID
