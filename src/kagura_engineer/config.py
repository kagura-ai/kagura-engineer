from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class ConfigError(Exception):
    """Raised when repo.yaml is missing, unparseable, or fails validation."""


# The Cloud-only fields that memory_backend="cloud" cannot function without.
# Single source of truth (issue #43): both the model validator below
# (_require_cloud_fields) and the CLI `init` affordance
# (cli._written_backend_needs_creds) iterate this tuple, so adding a required
# cloud field extends enforcement *and* the init next-step hint at once — no
# hand-synced duplicate field lists to drift apart.
CLOUD_REQUIRED_FIELDS: tuple[str, ...] = (
    "memory_cloud_url",
    "workspace_id",
    "context_id",
)


class ReviewConfig(BaseModel):
    # Reject unknown keys so a nested typo (e.g. `review.max_loopss`) fails loudly
    # at load time instead of being silently dropped (issue #45). Kept consistent
    # with Config below — forbid applies at every nesting level.
    model_config = ConfigDict(extra="forbid")

    models: list[str] = Field(default_factory=list)
    max_loops: int = 3


class Config(BaseModel):
    # Reject unknown top-level keys so a typo'd field (e.g. `workspace_idd`) fails
    # loudly at load time with the offending key named, rather than being silently
    # swallowed and resurfacing as a confusing downstream error (issue #45).
    model_config = ConfigDict(extra="forbid")

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
    # Brain backend (issue #51). "claude" (default) drives Claude Code; "codex"
    # drives the Codex CLI (incl. Ollama Cloud via brain_endpoint). The default
    # reproduces today's behaviour byte-for-byte.
    brain_backend: Literal["claude", "codex"] = "claude"
    # Optional caller-chosen endpoint (non-secret URL/alias only — NEVER a key):
    #   claude -> an Anthropic-compatible gateway URL
    #   codex  -> "ollama-cloud" (alias for Ollama Cloud) or an OpenAI-compatible URL
    # The API key is resolved from the KAGURA_BRAIN_API_KEY env var (see
    # run/brain_select.py), never from repo.yaml.
    brain_endpoint: str = ""
    # Codex in-task MCP policy seam (issue #68). kagura_brain >= 0.4.0 is
    # MCP-capable for codex, but the engineer keeps codex at no-in-task-MCP by
    # default as a harness policy ("capable but disabled by policy", from #51/#63).
    # Set true to forward the resolved MCP config to codex. Known flag-on caveats
    # are logged at select time (see run/brain_select.py): the path is not yet
    # smoke-verified end-to-end, and codex has no per-call tool allow-list, so
    # the MEMORY_TOOLS confinement claude gets does not apply there.
    enable_codex_mcp: bool = False

    def resolve_mcp_config(self, repo_root: str | Path) -> str | None:
        """Return the Claude Code MCP config path to attach for in-task recall.

        Precedence (issue #36):
          1. An explicit `memory_mcp_config` from repo.yaml wins verbatim — the
             user pointed it somewhere deliberately.
          2. Otherwise auto-discover the generated `<repo_root>/.mcp.json`
             (written by the setup `memory-mcp` step), so an autonomous run
             reaches the memory MCP tools with no hand-wiring.
          3. None when neither resolves — the run falls back to string-injected
             grounding only (the historical default).
        """
        if self.memory_mcp_config:
            return self.memory_mcp_config
        candidate = Path(repo_root) / ".mcp.json"
        if candidate.is_file():
            return str(candidate)
        return None

    @model_validator(mode="after")
    def _require_cloud_fields(self) -> "Config":
        """Re-require the Cloud-only fields when the backend is the Cloud.

        They default to "" at the field level (so a local-backend repo.yaml
        needs no credentials); the cloud backend cannot function without them,
        so demand them here with a clear message instead of failing later.
        """
        if self.memory_backend == "cloud":
            missing = [
                name for name in CLOUD_REQUIRED_FIELDS if not getattr(self, name)
            ]
            if missing:
                raise ValueError(
                    "memory_backend='cloud' requires: " + ", ".join(missing)
                )
        return self


def load_config(path: str | Path) -> Config:
    p = Path(path)
    if not p.is_file():
        # A missing repo.yaml is the fresh-checkout case — point the user at the
        # scaffold path (issue #35) instead of just reporting the absence.
        raise ConfigError(
            f"config not found: {p} — run `kagura-engineer init` to scaffold a repo.yaml"
        )
    try:
        text = p.read_text(encoding="utf-8")
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


@dataclass(frozen=True)
class ConfigLoad:
    """Outcome of a lenient config load (issue #71).

    The first-install seam: `doctor`/`setup` must operate on a fresh checkout
    that has no (or an incomplete) `repo.yaml` instead of refusing. This carries
    enough to drive a degraded report without re-deriving it:

      cfg     — the validated Config, or None when missing/unparseable/invalid
      error   — the ConfigError message when cfg is None (else None)
      missing — the file does not exist specifically (drives setup's
                auto-scaffold); an existing-but-invalid file has missing=False
    """

    cfg: Config | None
    error: str | None
    missing: bool


def load_config_lenient(path: str | Path) -> ConfigLoad:
    """Load `repo.yaml` without raising — the lenient counterpart of `load_config`.

    `load_config` stays the hard requirement for `run`/`goal`/`review`/`eval`
    (they cannot do anything useful without a valid config). This wrapper lets
    `doctor`/`setup` — whose whole job is "get me to a healthy state" — degrade
    gracefully on a missing/incomplete config instead of exiting on the spot.
    Never raises: every failure mode of `load_config` becomes a populated
    `ConfigLoad.error`.
    """
    p = Path(path)
    missing = not p.is_file()
    try:
        return ConfigLoad(cfg=load_config(p), error=None, missing=False)
    except ConfigError as exc:
        return ConfigLoad(cfg=None, error=str(exc), missing=missing)
