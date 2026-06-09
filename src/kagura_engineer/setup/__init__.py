"""`setup` orchestrator: the public entry point of the
`kagura_engineer setup` command.

The orchestrator is the only module in the `setup` package that
the CLI layer imports. The per-step modules (`git`, `claude`,
`gh`, `ollama`, `memory_cloud`) are private implementation
details — they are not part of the public API, and the
orchestrator owns the order, the time budget, and the exception
isolation policy.

Step order
----------

The canonical order is:

    git, claude-code, gh, ollama, ollama-models, memory-cloud

`git` is first because the worktree is the only one with a
filesystem precondition. `claude-code` and `gh` come next because
they are the auth-bound tools the rest of the pipeline leans on.
`ollama` (daemon up) precedes `ollama-models` (pull models) for
obvious reasons. `memory-cloud` is last because its check is
purely a reachability probe and does not influence the earlier
steps.

Per-step exception isolation
----------------------------

A buggy or partial step must not abort the rest of the plan —
the same invariant that doctor.registry.py enforces. The
orchestrator wraps each step call in a try/except and converts
any leak into a FAIL StepResult with the exception type in the
detail.

Output
------

A `SetupReport` with four buckets (ran / skipped / failed /
needs_user). The `is_blocked` property is the contract Plan 3
relies on: `run` will refuse to start when is_blocked is True.
The CLI layer (cli.py) maps is_blocked to exit code 2 (any
NEEDS_USER) or 1 (any FAIL) per the Plan 2 design doc §1.6.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from ..config import Config
from . import claude, gh, git, memory_cloud, memory_mcp, ollama
from .platform import detect
from .result import SetupReport, StepResult, StepStatus

_log = logging.getLogger(__name__)

# Canonical step order. Locked in by test_step_names_are_in_canonical_order.
STEP_NAMES: list[str] = [
    "git",
    "claude-code",
    "gh",
    "ollama",
    "ollama-models",
    "memory-cloud",
    "memory-mcp",
]

# Per-step registry: name -> callable. Each callable receives
# (platform, config, *, no_input, dry_run, full). The kwargs shape
# varies per step (e.g. ollama needs ollama_url, claude takes no
# config) — that variance is encoded in a thin wrapper closure below.
# `full` is consumed only by memory-mcp (the `--full` opt-in that
# additionally installs SDK hooks + skills); the rest ignore it.
_STEP_FNS: dict[str, Callable[..., StepResult]] = {
    "git": lambda platform, cfg, *, no_input, dry_run, full: git.ensure_git(
        platform, no_input=no_input, dry_run=dry_run
    ),
    "claude-code": lambda platform, cfg, *, no_input, dry_run, full: claude.ensure_claude_login(
        no_input=no_input, dry_run=dry_run
    ),
    "gh": lambda platform, cfg, *, no_input, dry_run, full: gh.ensure_gh_auth(
        platform, no_input=no_input, dry_run=dry_run
    ),
    "ollama": lambda platform, cfg, *, no_input, dry_run, full: ollama.ensure_ollama_up(
        platform, cfg.ollama_url, no_input=no_input, dry_run=dry_run
    ),
    "ollama-models": lambda platform, cfg, *, no_input, dry_run, full: ollama.pull_ollama_models(
        platform, cfg.ollama_url, cfg.review.models, no_input=no_input, dry_run=dry_run
    ),
    "memory-cloud": lambda platform, cfg, *, no_input, dry_run, full: memory_cloud.ensure_memory_cloud_reachable(
        cfg.memory_cloud_url, no_input=no_input, dry_run=dry_run
    ),
    "memory-mcp": lambda platform, cfg, *, no_input, dry_run, full: memory_mcp.ensure_memory_mcp_config(
        cfg, no_input=no_input, dry_run=dry_run, full=full
    ),
}


def build_plan(only: str | None = None) -> list[str]:
    """Return the ordered list of step names that will run, filtered
    by `--fix <name>` if provided.

    Note: returns names only. Each name is then resolved to its
    callable inside `run_plan` (so a `--fix` of a name that does
    not exist surfaces a clean empty plan, not an ImportError).
    """
    if only is None:
        return list(STEP_NAMES)
    return [name for name in STEP_NAMES if name == only]


def run_plan(
    cfg: Config,
    *,
    no_input: bool,
    dry_run: bool,
    only: str | None = None,
    full: bool = False,
) -> SetupReport:
    """Execute the build plan and aggregate results into a SetupReport.

    Steps run sequentially in the canonical order (or the single
    filtered step when `only` is set). Per-step exceptions are
    caught and converted to FAIL StepResults — a single broken
    step never aborts the rest of the plan.

    `full` is the `--full` opt-in: it reaches the memory-mcp step so the
    SDK additionally installs hooks + skills (default: `.mcp.json` only).
    """
    platform = detect()
    plan = build_plan(only=only)
    ran: list[StepResult] = []
    skipped: list[StepResult] = []
    failed: list[StepResult] = []
    needs_user: list[StepResult] = []
    overall_start = time.monotonic()

    for name in plan:
        fn = _STEP_FNS[name]
        try:
            result = fn(platform, cfg, no_input=no_input, dry_run=dry_run, full=full)
        except Exception as exc:  # noqa: BLE001 — see module docstring
            _log.exception("setup step %r raised", name)
            result = StepResult(
                name,
                StepStatus.FAIL,
                f"step raised {type(exc).__name__}: {exc}",
                fix_hint="this is a setup bug; please report it",
            )

        if result.status is StepStatus.OK:
            ran.append(result)
        elif result.status is StepStatus.SKIPPED:
            skipped.append(result)
        elif result.status is StepStatus.FAIL:
            failed.append(result)
        elif result.status is StepStatus.NEEDS_USER:
            needs_user.append(result)
        else:  # pragma: no cover — defensive, no other status exists
            failed.append(result)

    return SetupReport(
        ran=ran,
        skipped=skipped,
        failed=failed,
        needs_user=needs_user,
        duration_s=time.monotonic() - overall_start,
    )
