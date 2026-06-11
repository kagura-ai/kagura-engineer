"""Plan 4b — the auto-review/fix loop for `kagura-engineer review --fix`.

    review → red?  → claude -p fixes blocking findings + commits → re-review
                  → repeat until green/yellow, or the fix budget (max_loops)
                     is spent, or a fix/review step fails.

Roles stay clean (decision [[bounded-composable]]): the reviewer is bounded
(emits findings only); the ACTOR (`claude -p`, via `fixer.run_fixer`) does the
edits. This is a separate `review` entrypoint — `run` still never calls the
reviewer (boundary = PR).

Stop conditions:
  - review OK (green/yellow)  → OK (clean, possibly after N fixes)
  - review FAIL (infra)       → FAIL, and we do NOT fix (untrusted findings)
  - still red at max_loops    → BLOCKED (resumable; human takes over)
  - a fixer invocation fails  → FAIL

`review_pr` / `run_fixer` are imported at module scope so tests can
monkeypatch them on this module.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from ..config import Config, ConfigError
from ..mcp import memory_tool_ids
from ..run.brain_select import select_brain
from ..run.memory import MemoryClient, resolve_memory_client
from . import review_pr
from .fixer import build_fix_prompt, run_fixer
from .result import ReviewLoopReport, ReviewReport, ReviewStatus

_log = logging.getLogger(__name__)

_BLOCKING_SEVERITIES = {"HIGH", "CRITICAL"}


def review_fix_loop(
    cfg: Config,
    target: str = "HEAD",
    *,
    base: str = "main",
    max_loops: int | None = None,
    memory: MemoryClient | None = None,
    repo_root: Path | None = None,
) -> ReviewLoopReport:
    mem = memory if memory is not None else resolve_memory_client(cfg)
    # issue #56 (same deal as run_idea / issue #14): a cloud client holds a
    # persistent event loop + httpx client that hangs the process at exit if
    # never closed. We close ONLY a client we created — an injected one is the
    # caller's to own. The try/finally also covers exceptions that propagate
    # out of the loop (review_pr infra errors, non-OSError fixer failures).
    owns_mem = memory is None
    root = repo_root if repo_root is not None else Path.cwd()
    budget = cfg.review.max_loops if max_loops is None else max_loops
    started = time.monotonic()

    iterations: list[ReviewReport] = []
    attempts = 0

    def _finish(status: ReviewStatus, detail: str, resume_hint: str | None = None) -> ReviewLoopReport:
        return ReviewLoopReport(
            target=target, base=base, iterations=iterations, fixes_attempted=attempts,
            status=status, detail=detail, resume_hint=resume_hint,
            duration_s=time.monotonic() - started,
        )

    try:
        # Resolve the brain backend ONCE up-front (issue #51); whether MCP
        # wiring is forwarded is decided by select_brain's policy (codex only
        # with enable_codex_mcp, issue #68). A bad endpoint/key config is a
        # clean FAIL.
        try:
            brain_call = select_brain(cfg, os.environ)
        except ConfigError as exc:
            return _finish(ReviewStatus.FAIL, f"backend config error: {exc}")

        while True:
            rep = review_pr(cfg, target, base=base, memory=mem, repo_root=root)
            iterations.append(rep)

            if rep.status is ReviewStatus.OK:
                note = f" after {attempts} fix(es)" if attempts else ""
                return _finish(ReviewStatus.OK, f"clean ({rep.verdict}){note}")
            if rep.status is ReviewStatus.FAIL:
                # could not review — never fix on untrusted findings
                return _finish(ReviewStatus.FAIL, f"could not review: {rep.detail}")

            # rep.status is BLOCKED (red verdict)
            if attempts >= budget:
                return _finish(
                    ReviewStatus.BLOCKED,
                    f"still red after {attempts} fix attempt(s)",
                    resume_hint=f"review {rep.report_path or '.kagura/review.json'} and fix "
                                f"manually, then re-run `kagura-engineer review {target}`",
                )

            # Only the genuinely-blocking findings drive the fix; the full report
            # (report_path) still carries the rest for the actor to read if needed.
            blocking = [f for f in rep.findings if f.severity.upper() in _BLOCKING_SEVERITIES]
            mcp_config = cfg.resolve_mcp_config(root)
            prompt = build_fix_prompt(rep.report_path, blocking or rep.findings,
                                      mcp_enabled=brain_call.mcp_enabled(mcp_config),
                                      mcp_tools=memory_tool_ids(brain_call.backend))
            try:
                fix = run_fixer(root, prompt, brain_call=brain_call, mcp_config=mcp_config)
            except (OSError, ValueError) as exc:
                # ValueError: the codex adapter raises on a missing/non-JSON
                # mcp_config file — convert to a clean FAIL like OSError.
                _log.exception("review --fix could not launch %s", brain_call.backend)
                return _finish(
                    ReviewStatus.FAIL,
                    f"could not launch {brain_call.backend} for fix: {exc}",
                )
            attempts += 1
            if fix.returncode != 0:
                tail = "timed out" if fix.timed_out else (fix.stderr or "").strip()[-200:]
                return _finish(ReviewStatus.FAIL, f"fix attempt {attempts} failed: {tail}")
    finally:
        if owns_mem and hasattr(mem, "close"):
            try:
                mem.close()
            except Exception:  # noqa: BLE001 — teardown is best-effort
                _log.exception("review memory client close failed (non-fatal)")
