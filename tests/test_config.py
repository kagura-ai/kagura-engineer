from pathlib import Path

import pytest

import yaml
from pydantic import ValidationError

from kagura_engineer.config import CLOUD_REQUIRED_FIELDS, Config, load_config
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


def test_local_backend_allows_missing_cloud_fields(tmp_path):
    # With memory_backend=local the harness never touches Memory Cloud, so the
    # Cloud-only fields (memory_cloud_url/workspace_id/context_id) are optional.
    p = tmp_path / "repo.yaml"
    p.write_text("profile: coding\nmemory_backend: local\n")
    cfg = load_config(p)
    assert cfg.memory_backend == "local"
    assert cfg.profile == "coding"


def test_cloud_backend_requires_cloud_fields(tmp_path):
    # Default backend is cloud → the Cloud fields stay mandatory.
    p = tmp_path / "repo.yaml"
    p.write_text("profile: coding\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_cloud_required_fields_constant_is_stable():
    # issue #43: CLOUD_REQUIRED_FIELDS is the single source of truth for the
    # cloud-only mandatory fields, shared by the validator and the CLI init
    # affordance. Pin its contents so a change is a conscious edit.
    assert set(CLOUD_REQUIRED_FIELDS) == {
        "memory_cloud_url",
        "workspace_id",
        "context_id",
    }


@pytest.mark.parametrize("field", CLOUD_REQUIRED_FIELDS)
def test_each_cloud_required_field_is_enforced(tmp_path, field):
    # The validator must enforce *exactly* the fields in CLOUD_REQUIRED_FIELDS —
    # this ties _require_cloud_fields to the shared constant so adding a field to
    # the tuple automatically extends enforcement (no hand-sync).
    data = {
        "profile": "coding",
        "memory_backend": "cloud",
        "memory_cloud_url": "https://m",
        "workspace_id": "w",
        "context_id": "c",
    }
    data[field] = ""  # blank exactly one required field
    p = tmp_path / "repo.yaml"
    p.write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError, match=field):
        load_config(p)


def test_unknown_top_level_key_rejected(tmp_path, valid_repo_yaml_text):
    # issue #45: a typo'd / unknown top-level key (e.g. workspace_idd) must fail
    # loudly at load time with the offending key named, not be silently swallowed
    # and resurface later as a confusing downstream error.
    p = tmp_path / "repo.yaml"
    p.write_text(valid_repo_yaml_text + "workspace_idd: oops\n")
    with pytest.raises(ConfigError, match="workspace_idd"):
        load_config(p)


def test_unknown_nested_review_key_rejected(tmp_path, valid_repo_yaml_text):
    # issue #45: forbid applies to the nested ReviewConfig too — a typo like
    # review.max_loopss must be rejected, not silently dropped (otherwise the
    # "make typos fail loud" goal is inconsistent across nesting levels).
    p = tmp_path / "repo.yaml"
    p.write_text(valid_repo_yaml_text + "review:\n  max_loopss: 9\n")
    with pytest.raises(ConfigError, match="max_loopss"):
        load_config(p)


def test_known_keys_still_accepted_under_forbid(tmp_path, valid_repo_yaml_text):
    # Guard against over-tightening: every legitimate field (including the
    # optional ones and the nested review block) must still validate.
    p = tmp_path / "repo.yaml"
    p.write_text(
        valid_repo_yaml_text
        + "ollama_url: http://localhost:11434\n"
        + "memory_mcp_config: .mcp.json\n"
        + "memory_failover: false\n"
        + "review:\n  models: [haiku]\n  max_loops: 5\n"
    )
    cfg = load_config(p)
    assert cfg.review.max_loops == 5
    assert cfg.memory_failover is False


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.yaml")


def test_missing_file_message_points_at_init(tmp_path):
    # A fresh checkout has no repo.yaml; the error must tell the user how to
    # scaffold one (issue #35) rather than just reporting the absence.
    with pytest.raises(ConfigError, match="init"):
        load_config(tmp_path / "repo.yaml")


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


def test_memory_failover_defaults_true():
    from kagura_engineer.config import Config
    cfg = Config(profile="coding", memory_cloud_url="https://m", workspace_id="w", context_id="c")
    assert cfg.memory_failover is True


def test_memory_failover_can_be_disabled():
    from kagura_engineer.config import Config
    cfg = Config(
        profile="coding", memory_cloud_url="https://m", workspace_id="w",
        context_id="c", memory_failover=False,
    )
    assert cfg.memory_failover is False


# --- resolve_mcp_config (issue #36) ------------------------------------


def _cloud_cfg(**over):
    from kagura_engineer.config import Config
    base = dict(profile="coding", memory_cloud_url="https://m", workspace_id="w", context_id="c")
    base.update(over)
    return Config(**base)


def test_resolve_mcp_config_explicit_override_wins(tmp_path):
    # An explicit memory_mcp_config in repo.yaml is honoured verbatim, even
    # when a generated .mcp.json also exists.
    (tmp_path / ".mcp.json").write_text("{}")
    cfg = _cloud_cfg(memory_mcp_config="/custom/path.json")
    assert cfg.resolve_mcp_config(tmp_path) == "/custom/path.json"


def test_resolve_mcp_config_discovers_generated_file(tmp_path):
    # No explicit config → auto-discover the generated <repo>/.mcp.json so an
    # autonomous run reaches memory tools without hand-wiring.
    gen = tmp_path / ".mcp.json"
    gen.write_text("{}")
    cfg = _cloud_cfg()
    assert cfg.resolve_mcp_config(tmp_path) == str(gen)


def test_resolve_mcp_config_none_when_absent(tmp_path):
    cfg = _cloud_cfg()
    assert cfg.resolve_mcp_config(tmp_path) is None


# --- brain backend selection (issue #51) -------------------------------


def _minimal_local() -> dict:
    # local backend needs no cloud creds — keeps these tests focused on the new fields
    return {"profile": "p", "memory_backend": "local"}


def test_brain_backend_defaults_to_claude_no_endpoint():
    cfg = Config.model_validate(_minimal_local())
    assert cfg.brain_backend == "claude"
    assert cfg.brain_endpoint == ""


def test_brain_backend_accepts_codex_and_endpoint():
    cfg = Config.model_validate(
        {**_minimal_local(), "brain_backend": "codex", "brain_endpoint": "ollama-cloud"}
    )
    assert cfg.brain_backend == "codex"
    assert cfg.brain_endpoint == "ollama-cloud"


def test_brain_backend_rejects_unknown_value():
    with pytest.raises(ValidationError):  # the Literal["claude","codex"] constraint
        Config.model_validate({**_minimal_local(), "brain_backend": "gpt"})


def test_unknown_brain_key_still_forbidden():
    with pytest.raises(ValidationError):  # extra="forbid" rejects the typo'd key
        Config.model_validate({**_minimal_local(), "brain_backendd": "codex"})
