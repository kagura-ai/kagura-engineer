from __future__ import annotations

import logging
from pathlib import Path

from ..config import Config
from . import checks
from .result import CheckResult, Status

_log = logging.getLogger(__name__)

_WORST = {Status.OK: 0, Status.WARN: 1, Status.FAIL: 2}

# Each check is a no-arg thunk so the orchestrator can wrap it in a generic
# try/except. A buggy or partial check must not abort the entire doctor run —
# surface the failure as a FAIL CheckResult so the user sees the full picture.
_CHECKS: list[tuple[str, callable]] = [
    ("git", lambda c: checks.check_git()),
    # The brain backend's CLI: codex when selected, claude otherwise. This
    # occupies the same slot the unconditional claude-code check used to —
    # ordering of every other check is unchanged.
    ("brain-cli", lambda c: (
        checks.check_codex()
        if c.brain_backend == "codex"
        else checks.check_claude_code()
    )),
    ("gh", lambda c: checks.check_gh()),
    ("ollama", lambda c: checks.check_ollama(c.ollama_url, required=c.review.models)),
    ("haiku", lambda c: checks.check_haiku()),
    # "memory" is the generic group/crash-fallback label; the concrete check
    # emits the backend-specific display name ("memory-cloud" / "memory-local")
    # on success — "memory" only appears if the check itself raises.
    ("memory", lambda c: (
        checks.check_local_memory(c.local_memory_path)
        if c.memory_backend == "local"
        else checks.check_memory_cloud(c.memory_cloud_url)
    )),
    ("gh-issue-driven", lambda c: checks.check_gh_issue_driven()),
]

# Cloud-only checks: appended to the plan only when the backend is the Cloud.
# The offline SQLite backend has no MCP memory server, so the generated
# .mcp.json check (issue #36) is meaningless for it.
_CLOUD_ONLY_CHECKS: list[tuple[str, callable]] = [
    ("memory-mcp", lambda c: checks.check_memory_mcp(Path.cwd())),
    # issue #70: live-resolve config.context_id to its context NAME so a
    # wildcard/stale binding pointing recall at the wrong context is caught
    # pre-flight. Meaningless for the local backend (no cloud context).
    ("memory-context", lambda c: checks.check_memory_context(c)),
]


def run_all(cfg: Config) -> list[CheckResult]:
    results: list[CheckResult] = []
    plan = list(_CHECKS)
    if cfg.memory_backend == "cloud":
        plan += _CLOUD_ONLY_CHECKS
    for name, fn in plan:
        try:
            results.append(fn(cfg))
        except Exception as exc:  # noqa: BLE001 — see docstring above
            _log.exception("doctor check %r raised", name)
            results.append(
                CheckResult(
                    name,
                    Status.FAIL,
                    f"check raised {type(exc).__name__}: {exc}",
                    "this is a doctor bug; please report it",
                )
            )
    return results


def overall_status(results: list[CheckResult]) -> Status:
    if not results:
        return Status.OK
    return max(results, key=lambda r: _WORST[r.status]).status
