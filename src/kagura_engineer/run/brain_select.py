"""Resolve Config + env into the chosen kagura-brain backend (issues #51, #63).

`select_brain` maps `brain_backend`/`brain_endpoint` + env onto a `BrainCall`
shim over `kagura_brain.select` (issue #63: the generic claude/codex dispatch now
lives in the library, so it is not re-implemented per consumer). The shim keeps
the two engineer-specific concerns the pure library `BrainHandle` does not carry:

  * `.backend` — surfaced in run/review error and log messages.
  * `.mcp_enabled()` / `supports_mcp` — drives the PROMPT BUILDER (whether to tell
    the child it has in-task MCP recall). For codex this is ENGINEER POLICY, not
    library capability: kagura_brain 0.4.0's codex adapter CAN wire MCP (it
    translates ``.mcp.json`` into ``-c mcp_servers.*`` overrides), but the harness
    keeps codex at no-in-task-MCP unless the operator opts in via the explicit
    ``enable_codex_mcp`` config seam (issue #68). The library `BrainHandle`
    *forbids* a codex handle with ``supports_mcp=False`` (it fails closed), so the
    policy override lives in the shim, not in the handle.

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
    `supports_mcp` is the engineer's view of in-task MCP availability — always on
    for claude, and for codex governed by the `enable_codex_mcp` config seam
    (issue #68; off by default). `invoke` forwards the MCP config + our
    `MEMORY_TOOLS` only for a backend the engineer enables MCP for.
    """

    backend: str
    _handle: BrainHandle
    # ENGINEER POLICY, NOT library capability. This may DIVERGE from
    # `_handle.supports_mcp`, which is the library's per-backend *capability* flag
    # (True for both claude and codex in kagura_brain 0.4.0). For codex the value
    # comes from the `enable_codex_mcp` config seam (issue #68, default off), so
    # `mcp_enabled()`/`invoke()` MUST gate on THIS field, never on
    # `_handle.supports_mcp` — reading the handle's flag would silently re-enable
    # codex MCP for operators who did not opt in. Change the policy in repo.yaml
    # (never the handle). See the module docstring.
    supports_mcp: bool

    def mcp_enabled(self, mcp_config: str | None) -> bool:
        """Whether in-task MCP recall is actually live for this call — used by the
        prompt builder. False for codex unless `enable_codex_mcp` opted in, and
        always False without a resolved mcp_config.

        Gates on `self.supports_mcp` (engineer policy), NOT `_handle.supports_mcp`
        (library capability) — see the field comment."""
        return self.supports_mcp and bool(mcp_config)

    def invoke(
        self, prompt: str, *, cwd: Path | None, timeout: int,
        mcp_config: str | None = None,
    ) -> BrainResult:
        # The handle already carries endpoint/api_key (resolved in select_brain);
        # we only add the MCP wiring, and only for a backend the engineer enables
        # it for — codex only when `enable_codex_mcp` opted in (issue #68).
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
        # The library is pure (no logging); the engineer keeps an operator signal
        # either way: opted in -> the flag-on path is not yet smoke-verified
        # end-to-end; default off -> codex still grounds out-of-band only.
        if cfg.enable_codex_mcp:
            _log.warning(
                "enable_codex_mcp=true: forwarding in-task MCP wiring to codex — "
                "end-to-end grounding via the codex adapter is not yet "
                "smoke-verified (issue #68)"
            )
        else:
            _log.warning(
                "brain_backend=codex: kagura-engineer does not enable in-task MCP "
                "memory tools for codex (enable_codex_mcp is off); grounding is "
                "out-of-band recall only"
            )
        return BrainCall("codex", handle, supports_mcp=cfg.enable_codex_mcp)
    return BrainCall("claude", handle, supports_mcp=True)
