"""ExecutionProfile — the resolved execution-profile SSOT (issue #70).

Operators of headless `run`/`goal`/`review`/`eval` could not see which
model/provider a run would actually use, nor which memory context it recalls
from / persists to (a wildcard config binding once silently routed recall to
the wrong context). `resolve_profile` distills Config + env + repo_root into
one frozen `ExecutionProfile`, surfaced through three outlets: the doctor
profile block, the per-command startup header, and the `"profile"` object in
every `--json` report.

`resolve_profile` is pure — no network, no subprocess. The brain fields are
read off `select_brain`'s result (the exact code path `run`/`review --fix`
execute, cheap: `kagura_brain.select` builds a handle without I/O), so the
display can never diverge from execution; the codex half-configured-pair
`ConfigError` is preserved, and every CLI entry already catches ConfigError.

`render_lines` / `to_dict` are the single formatting SSOT: every text outlet
prints these lines, every JSON outlet embeds this dict.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import Config
from .run.brain_select import select_brain


@dataclass(frozen=True)
class ExecutionProfile:
    brain_backend: str          # "claude" | "codex"
    brain_endpoint: str | None  # None → "default" in rendering
    brain_mcp: bool             # the BrainCall in-task-MCP POLICY (engineer's, not the lib's)
    reviewer_model: str | None  # cfg.review.models[0] or None (reviewer default)
    ollama_url: str
    memory_backend: str         # "cloud" | "local"
    workspace_id: str           # "" for local
    context_id: str             # "" for local
    memory_mcp_config: str | None
    memory_failover: bool


def resolve_profile(
    cfg: Config, env: Mapping[str, str], repo_root: Path
) -> ExecutionProfile:
    """Resolve Config + env into the profile a run would execute with.

    Raises only ConfigError (the codex half-pair case, via select_brain)."""
    call = select_brain(cfg, env)
    cloud = cfg.memory_backend == "cloud"
    return ExecutionProfile(
        brain_backend=call.backend,
        brain_endpoint=cfg.brain_endpoint or None,
        brain_mcp=call.supports_mcp,
        reviewer_model=cfg.review.models[0] if cfg.review.models else None,
        ollama_url=cfg.ollama_url,
        memory_backend=cfg.memory_backend,
        # A local-backend repo.yaml may carry stale cloud ids — never display
        # identifiers a run would not actually use.
        workspace_id=cfg.workspace_id if cloud else "",
        context_id=cfg.context_id if cloud else "",
        memory_mcp_config=cfg.resolve_mcp_config(repo_root),
        memory_failover=cfg.memory_failover,
    )


def render_lines(profile: ExecutionProfile, *, brain: bool = True) -> list[str]:
    """The human form of the profile — shared by every text outlet.

    `brain=False` omits the brain line (plain `review` runs no brain — the
    header must not imply one)."""
    lines: list[str] = []
    if brain:
        lines.append(
            f"brain: {profile.brain_backend} "
            f"(endpoint: {profile.brain_endpoint or 'default'}, "
            f"in-task MCP: {'on' if profile.brain_mcp else 'off'})"
        )
    lines.append(
        f"reviewer: {profile.reviewer_model or 'default'} @ {profile.ollama_url}"
    )
    if profile.memory_backend == "cloud":
        lines.append(
            f"memory: cloud · workspace={profile.workspace_id} · "
            f"context={profile.context_id} · "
            f"failover={'on' if profile.memory_failover else 'off'} · "
            f"mcp={profile.memory_mcp_config or 'none'}"
        )
    else:
        lines.append("memory: local")
    return lines


def to_dict(profile: ExecutionProfile) -> dict:
    """The JSON form of the profile (the `"profile"` object in every report)."""
    return asdict(profile)


def to_dict_or_none(profile: ExecutionProfile | None) -> dict | None:
    """Null-safe `to_dict` — the single embed form every report renderer uses
    (a report built outside the CLI carries no profile; render `null`)."""
    return to_dict(profile) if profile else None
