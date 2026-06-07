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
import time
from pathlib import Path

from ..config import Config
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
        prompt = build_fix_prompt(rep.report_path, blocking or rep.findings)
        try:
            fix = run_fixer(root, prompt)
        except OSError as exc:
            _log.exception("review --fix could not launch claude")
            return _finish(ReviewStatus.FAIL, f"could not launch claude for fix: {exc}")
        attempts += 1
        if fix.returncode != 0:
            tail = "timed out" if fix.timed_out else (fix.stderr or "").strip()[-200:]
            return _finish(ReviewStatus.FAIL, f"fix attempt {attempts} failed: {tail}")
