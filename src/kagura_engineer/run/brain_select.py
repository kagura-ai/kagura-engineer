"""Resolve Config + env into the chosen kagura-brain backend (issues #51, #63).

`select_brain` maps `brain_backend`/`brain_endpoint` + env onto a `BrainCall`
shim over `kagura_brain.select` (issue #63: the generic claude/codex dispatch now
lives in the library, so it is not re-implemented per consumer). The shim exists
for ONE engineer-specific concern the library handle cannot carry — this is the
canonical statement of the policy; the other comments in this file point here:

  * `supports_mcp` / `.mcp_enabled()` — the ENGINEER'S in-task-MCP POLICY, not
    library capability. kagura_brain 0.4.0's codex adapter CAN wire MCP (it
    translates ``.mcp.json`` into ``-c mcp_servers.*`` overrides), but the harness
    keeps codex at no-in-task-MCP unless the operator opts in via the explicit
    ``enable_codex_mcp`` config seam (issue #68). The library `BrainHandle`
    *forbids* a codex handle with ``supports_mcp=False`` (it fails closed), so the
    policy override lives in the shim, not in the handle. Everything here MUST
    gate on the shim's `supports_mcp`, never on `_handle.supports_mcp` — reading
    the handle's flag would silently re-enable codex MCP for operators who did
    not opt in. Flag-on codex receives the MCP config ONLY: the codex adapter
    has no per-call tool allow-list (it accepts-and-drops `allowed_tools`), so
    the `MEMORY_TOOLS` confinement claude gets via `--allowedTools` does not
    apply there.

(`.backend` is kept as a convenience alias for log/error messages; the library
handle carries the same value.)

The API key is read from the env (`KAGURA_BRAIN_API_KEY`, the library-owned name
`kagura_brain.BRAIN_API_KEY_ENV`) consumer-side and passed to `select`; the
library never reads env, so a secret never lands in a committed repo.yaml
(issue #47). The half-configured-pair `ConfigError`s (endpoint without key, key
without endpoint) stay consumer-side, raised before `select` — the library's
both-or-neither rule would otherwise surface only at the first invoke, mid-run.
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
    """A resolved backend: a `kagura_brain` handle plus the engineer's MCP policy
    (`supports_mcp` / `mcp_enabled()` — see the module docstring) and `.backend`
    for run/review messages."""

    backend: str
    _handle: BrainHandle
    # ENGINEER POLICY, NOT library capability — may DIVERGE from
    # `_handle.supports_mcp`. Gate on THIS field, never on the handle's;
    # change the policy in repo.yaml (`enable_codex_mcp`), never the handle.
    # See the module docstring.
    supports_mcp: bool

    def mcp_enabled(self, mcp_config: str | None) -> bool:
        """Whether in-task MCP recall is actually live for this call (policy on
        AND a config resolved) — drives the prompt builder and `invoke`'s wiring.
        See the module docstring."""
        return self.supports_mcp and bool(mcp_config)

    def invoke(
        self, prompt: str, *, cwd: Path | None, timeout: int,
        mcp_config: str | None = None,
    ) -> BrainResult:
        # The handle already carries endpoint/api_key (resolved in select_brain);
        # we add the MCP wiring only when it is actually live (policy on AND a
        # config resolved) — forwarding kwargs with no config would only trip
        # the codex adapter's dropped-allow-list warning on a run with zero
        # MCP wiring.
        if self.mcp_enabled(mcp_config):
            if self.backend == "codex":
                # codex has no per-call tool allow-list (the adapter
                # accepts-and-drops `allowed_tools`), so forward the config
                # only; tool confinement relies on codex's own
                # sandbox/approval model. See the module docstring.
                return self._handle.invoke(
                    prompt, cwd=cwd, timeout=timeout, mcp_config=mcp_config,
                )
            return self._handle.invoke(
                prompt, cwd=cwd, timeout=timeout,
                mcp_config=mcp_config, allowed_tools=MEMORY_TOOLS,
            )
        return self._handle.invoke(prompt, cwd=cwd, timeout=timeout)


def select_brain(cfg: Config, env: Mapping[str, str]) -> BrainCall:
    """Resolve the brain backend from Config + env. Raises ConfigError when the
    endpoint/API-key pair is half-configured (either half without the other)."""
    endpoint = cfg.brain_endpoint or None
    api_key = (env.get(BRAIN_API_KEY_ENV) or "").strip() or None
    if endpoint and api_key is None:
        raise ConfigError(
            f"brain_endpoint={endpoint!r} requires an API key — "
            f"export {BRAIN_API_KEY_ENV}=... (it is never read from repo.yaml)"
        )
    if api_key and endpoint is None:
        # The library's BYO-endpoint rule is both-or-neither and raises only at
        # the first invoke (mid-run, past every clean-FAIL handler) — fail the
        # half-config here instead, where ConfigError is handled cleanly.
        raise ConfigError(
            f"{BRAIN_API_KEY_ENV} is set but brain_endpoint is not — set "
            "brain_endpoint in repo.yaml or unset the env var"
        )
    backend = "codex" if cfg.brain_backend == "codex" else "claude"
    handle = kagura_brain.select(backend, endpoint=endpoint, api_key=api_key)
    if backend == "codex":
        # The library is pure (no logging); the engineer keeps an operator
        # signal either way (select-time, so phrased conditionally — whether an
        # MCP config actually resolves is only known later, per invoke).
        if cfg.enable_codex_mcp:
            _log.warning(
                "enable_codex_mcp=true: codex will get in-task MCP wiring when "
                "an MCP config resolves — this path is not yet smoke-verified "
                "end-to-end, and codex cannot enforce the memory-tool "
                "allow-list (no per-call allow-list; confinement falls to "
                "codex's own approval model)"
            )
        else:
            _log.warning(
                "brain_backend=codex: kagura-engineer does not enable in-task MCP "
                "memory tools for codex (enable_codex_mcp is off); grounding is "
                "out-of-band recall only"
            )
        return BrainCall("codex", handle, supports_mcp=cfg.enable_codex_mcp)
    return BrainCall("claude", handle, supports_mcp=True)
