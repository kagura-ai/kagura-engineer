from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError


class ConfigError(Exception):
    """Raised when repo.yaml is missing, unparseable, or fails validation."""


class ReviewConfig(BaseModel):
    models: list[str] = Field(default_factory=list)
    max_loops: int = 3


class Config(BaseModel):
    profile: str
    memory_cloud_url: str
    # Memory Cloud filter hierarchy: workspace_id -> context_id -> memory.
    # workspace_id scopes all memory writes/recalls for this project; the
    # API key (resolved at the client layer) is also workspace-scoped.
    workspace_id: str
    context_id: str
    ollama_url: str = "http://localhost:11434"
    review: ReviewConfig = Field(default_factory=ReviewConfig)


def load_config(path: str | Path) -> Config:
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"config not found: {p}")
    try:
        data = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML: {exc}") from exc
    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"config validation failed: {exc}") from exc
