import pytest

from kagura_engineer.config import Config, load_config


def test_load_minimal_yaml(tmp_path):
    p = tmp_path / "repo.yaml"
    p.write_text(
        "profile: coding\n"
        "memory_cloud_url: https://memory.kagura-ai.com\n"
        "context_id: 550e8400-e29b-41d4-a716-446655440000\n"
    )
    cfg = load_config(p)
    assert cfg.profile == "coding"
    assert cfg.review.models == []
    assert cfg.review.max_loops == 3
    assert cfg.ollama_url == "http://localhost:11434"


def test_review_models_override(tmp_path):
    p = tmp_path / "repo.yaml"
    p.write_text(
        "profile: coding\n"
        "memory_cloud_url: https://memory.kagura-ai.com\n"
        "context_id: 550e8400-e29b-41d4-a716-446655440000\n"
        "review:\n"
        "  models: [qwen2.5-coder:7b, haiku]\n"
        "  max_loops: 5\n"
    )
    cfg = load_config(p)
    assert cfg.review.models == ["qwen2.5-coder:7b", "haiku"]
    assert cfg.review.max_loops == 5


def test_missing_required_field_raises(tmp_path):
    from kagura_engineer.config import ConfigError

    p = tmp_path / "repo.yaml"
    p.write_text("profile: coding\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_missing_file_raises(tmp_path):
    from kagura_engineer.config import ConfigError

    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.yaml")


def test_malformed_yaml_raises_config_error(tmp_path):
    from kagura_engineer.config import ConfigError

    p = tmp_path / "repo.yaml"
    p.write_text(
        "profile: coding\n\tbad: tab\n"
    )  # tab indentation → YAML scanner error
    with pytest.raises(ConfigError):
        load_config(p)


def test_missing_file_raises_config_error(tmp_path):
    from kagura_engineer.config import ConfigError

    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.yaml")


def test_missing_required_field_raises_config_error(tmp_path):
    from kagura_engineer.config import ConfigError

    p = tmp_path / "repo.yaml"
    p.write_text("profile: coding\n")
    with pytest.raises(ConfigError):
        load_config(p)
