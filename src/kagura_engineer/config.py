from __future__ import annotations

from pathlib import Path
from typing import Literal

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
    # Memory backend: "cloud" (Kagura Memory Cloud SDK) or "local" (offline
    # SQLite, no API key). `local_memory_path` is used only when backend=local.
    memory_backend: Literal["cloud", "local"] = "cloud"
    local_memory_path: str = ".kagura/memory.db"


def load_config(path: str | Path) -> Config:
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"config not found: {p}")
    try:
        text = p.read_text()
    except OSError as exc:
        # Present but unreadable (mode 000, foreign owner, non-traversable
        # dir). The docstring promises ConfigError for an unreadable config;
        # the CLI only catches ConfigError, so a raw OSError would crash.
        raise ConfigError(f"could not read config {p}: {exc}") from exc
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML: {exc}") from exc
    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"config validation failed: {exc}") from exc
