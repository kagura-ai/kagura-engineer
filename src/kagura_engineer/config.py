from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator


class ConfigError(Exception):
    """Raised when repo.yaml is missing, unparseable, or fails validation."""


class ReviewConfig(BaseModel):
    models: list[str] = Field(default_factory=list)
    max_loops: int = 3


class Config(BaseModel):
    profile: str
    # Cloud-only fields. Optional at the field level so an offline
    # (memory_backend=local) repo.yaml needs no Memory Cloud credentials; the
    # model validator below re-requires them when memory_backend == "cloud".
    memory_cloud_url: str = ""
    # Memory Cloud filter hierarchy: workspace_id -> context_id -> memory.
    # workspace_id scopes all memory writes/recalls for this project; the
    # API key (resolved at the client layer) is also workspace-scoped.
    workspace_id: str = ""
    context_id: str = ""
    ollama_url: str = "http://localhost:11434"
    review: ReviewConfig = Field(default_factory=ReviewConfig)
    # Memory backend: "cloud" (Kagura Memory Cloud SDK) or "local" (offline
    # SQLite, no API key). `local_memory_path` is used only when backend=local.
    memory_backend: Literal["cloud", "local"] = "cloud"
    local_memory_path: str = ".kagura/memory.db"
    # Optional path to a Claude Code MCP config (JSON {"mcpServers": {...}})
    # exposing a kagura-memory server. When set, headless `claude -p` phases get
    # the memory MCP tools attached for in-task recall (default: string injection
    # only). The server's tools must be permitted in your Claude settings.
    memory_mcp_config: str | None = None
    # issue: failover memory. When the cloud backend is active, wrap the cloud
    # client so critical writes (savepoint remember + set_state) that fail during
    # a Cloud outage are buffered to a local WAL and replayed on the next run.
    # Default on for resilience; set false to use the bare cloud client.
    memory_failover: bool = True

    @model_validator(mode="after")
    def _require_cloud_fields(self) -> "Config":
        """Re-require the Cloud-only fields when the backend is the Cloud.

        They default to "" at the field level (so a local-backend repo.yaml
        needs no credentials); the cloud backend cannot function without them,
        so demand them here with a clear message instead of failing later.
        """
        if self.memory_backend == "cloud":
            missing = [
                name
                for name in ("memory_cloud_url", "workspace_id", "context_id")
                if not getattr(self, name)
            ]
            if missing:
                raise ValueError(
                    "memory_backend='cloud' requires: " + ", ".join(missing)
                )
        return self


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
