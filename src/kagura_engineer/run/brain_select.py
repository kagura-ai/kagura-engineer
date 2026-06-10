"""Resolve Config + env into the chosen kagura-brain backend (issue #51).

`select_brain` is the single point that maps `brain_backend`/`brain_endpoint`
to a `kagura_brain` adapter and its per-backend kwargs, confining the
claude/codex MCP asymmetry to one place:

  * claude — supports MCP memory tools (`mcp_config` + `allowed_tools`) and an
    Anthropic-compatible BYO endpoint.
  * codex  — takes endpoint/api_key (Ollama Cloud / BYO) but NOT MCP tools; the
    codex adapter cannot accept them today, so an in-task recall is unavailable
    and grounding falls back to engineer's out-of-band recall. Logged once.

The API key is read from the env (KAGURA_BRAIN_API_KEY), never repo.yaml, so a
secret never lands in a committed config (cf. issue #47 / the memory-mcp setup).
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from kagura_brain import claude, codex
from kagura_brain.core import BrainResult

from ..config import Config, ConfigError
from ..mcp import MEMORY_TOOLS

_log = logging.getLogger(__name__)

#: Env var supplying the API key for a BYO/Ollama-Cloud endpoint. Kept out of
#: repo.yaml so a key is never committed.
BRAIN_API_KEY_ENV = "KAGURA_BRAIN_API_KEY"


@dataclass(frozen=True)
class BrainCall:
    """A resolved backend: the adapter's `invoke` plus per-backend kwargs.

    `invoke` forwards the common args and adds the kwargs the chosen backend
    accepts — MCP tools for claude, none for codex.
    """

    backend: str
    _invoke: Callable[..., BrainResult]
    supports_mcp: bool
    endpoint: str | None = None
    api_key: str | None = None

    def mcp_enabled(self, mcp_config: str | None) -> bool:
        """Whether in-task MCP recall is actually live for this call — used by the
        prompt builder. False for codex regardless of a resolved mcp_config."""
        return self.supports_mcp and bool(mcp_config)

    def invoke(
        self, prompt: str, *, cwd: Path | None, timeout: int,
        mcp_config: str | None = None,
    ) -> BrainResult:
        kwargs: dict[str, object] = {}
        if self.endpoint:
            kwargs["endpoint"] = self.endpoint
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.supports_mcp:
            kwargs["mcp_config"] = mcp_config
            kwargs["allowed_tools"] = MEMORY_TOOLS
        return self._invoke(prompt, cwd=cwd, timeout=timeout, **kwargs)


def select_brain(cfg: Config, env: Mapping[str, str]) -> BrainCall:
    """Resolve the brain backend from Config + env. Raises ConfigError when an
    endpoint is set but no API key is available in the env."""
    endpoint = cfg.brain_endpoint or None
    api_key = (env.get(BRAIN_API_KEY_ENV) or "").strip() or None
    if endpoint and api_key is None:
        raise ConfigError(
            f"brain_endpoint={endpoint!r} requires an API key — "
            f"export {BRAIN_API_KEY_ENV}=... (it is never read from repo.yaml)"
        )
    if cfg.brain_backend == "codex":
        _log.warning(
            "brain_backend=codex: no in-task MCP memory tools; grounding is "
            "out-of-band recall only (codex adapter has no MCP wiring yet)"
        )
        return BrainCall(
            "codex", codex.invoke, supports_mcp=False,
            endpoint=endpoint, api_key=api_key,
        )
    return BrainCall(
        "claude", claude.invoke, supports_mcp=True,
        endpoint=endpoint, api_key=api_key,
    )
