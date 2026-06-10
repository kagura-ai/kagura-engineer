"""Resolve Config + env into the chosen kagura-brain backend (issues #51, #63).

`select_brain` maps `brain_backend`/`brain_endpoint` + env onto a `BrainCall`
shim over `kagura_brain.select` (issue #63: the generic claude/codex dispatch now
lives in the library, so it is not re-implemented per consumer). The shim keeps
the two engineer-specific concerns the pure library `BrainHandle` does not carry:

  * `.backend` — surfaced in run/review error and log messages.
  * `.mcp_enabled()` / `supports_mcp` — drives the PROMPT BUILDER (whether to tell
    the child it has in-task MCP recall). We deliberately keep codex at
    ``supports_mcp=False`` here even though kagura_brain 0.4.0's codex adapter CAN
    wire MCP (it translates ``.mcp.json`` into ``-c mcp_servers.*`` overrides):
    enabling in-task MCP for codex is a behavior change tracked separately, not
    part of this refactor. The library `BrainHandle` *forbids* a codex handle with
    ``supports_mcp=False`` (it fails closed), so this capability override lives in
    the shim, not in the handle.

The API key is read from the env (`KAGURA_BRAIN_API_KEY`, the library-owned name
`kagura_brain.BRAIN_API_KEY_ENV`) consumer-side and passed to `select`; the
library never reads env, so a secret never lands in a committed repo.yaml
(issue #47). The endpoint-set-but-no-key `ConfigError` stays consumer-side,
raised before `select`.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import kagura_brain
from kagura_brain import BRAIN_API_KEY_ENV, BrainHandle
from kagura_brain.core import BrainResult

from ..config import Config, ConfigError
from ..mcp import MEMORY_TOOLS

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BrainCall:
    """A resolved backend: a `kagura_brain` handle plus engineer-side concerns.

    Wraps the library `BrainHandle` (which is invoke-only) to retain `.backend`
    (for run/review messages) and `.mcp_enabled()` (for the prompt builder).
    `supports_mcp` is the engineer's view of in-task MCP availability — claude
    only; see the module docstring for why codex stays False despite the library's
    capability. `invoke` forwards the MCP config + our `MEMORY_TOOLS` only for a
    backend the engineer enables MCP for.
    """

    backend: str
    _handle: BrainHandle
    supports_mcp: bool

    def mcp_enabled(self, mcp_config: str | None) -> bool:
        """Whether in-task MCP recall is actually live for this call — used by the
        prompt builder. False for codex regardless of a resolved mcp_config."""
        return self.supports_mcp and bool(mcp_config)

    def invoke(
        self, prompt: str, *, cwd: Path | None, timeout: int,
        mcp_config: str | None = None,
    ) -> BrainResult:
        # The handle already carries endpoint/api_key (resolved in select_brain);
        # we only add the MCP wiring, and only for a backend the engineer enables
        # it for. codex → forward neither mcp_config nor allowed_tools.
        if self.supports_mcp:
            return self._handle.invoke(
                prompt, cwd=cwd, timeout=timeout,
                mcp_config=mcp_config, allowed_tools=MEMORY_TOOLS,
            )
        return self._handle.invoke(prompt, cwd=cwd, timeout=timeout)


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
    backend = "codex" if cfg.brain_backend == "codex" else "claude"
    handle = kagura_brain.select(backend, endpoint=endpoint, api_key=api_key)
    if backend == "codex":
        # The library is pure (no logging) and 0.4.0 made codex MCP-capable; keep
        # the engineer's operator signal that this harness still grounds codex
        # out-of-band only (we do not enable codex in-task MCP — see module docs).
        _log.warning(
            "brain_backend=codex: kagura-engineer does not enable in-task MCP "
            "memory tools for codex; grounding is out-of-band recall only"
        )
        return BrainCall("codex", handle, supports_mcp=False)
    return BrainCall("claude", handle, supports_mcp=True)
