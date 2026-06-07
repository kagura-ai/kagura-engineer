"""Plan 4 `review` — launch kagura-code-reviewer, gate on its JSON verdict.

`review_pr` is a single shot (no auto-fix loop in v1):

    recall   → load_pinned + recall → untrusted --context-file grounding
    review   → run kagura-code-reviewer --format json, read the envelope
    gate     → evaluate(verdict): green/yellow → OK, red/unknown → BLOCKED

`run` never calls this — `review` is a separate entrypoint invoked after a
PR exists (boundary = PR). External boundaries (memory SDK, reviewer
subprocess) are wrapped so an infrastructure error returns a clean FAIL
ReviewReport, the same isolation invariant run/setup/doctor enforce.
`run_reviewer` / `resolve_head` are imported at module scope so tests can
monkeypatch them on the package.
"""
from __future__ import annotations

import logging
import tempfile
import time
from pathlib import Path

from ..config import Config
from ..run.gate import evaluate
from ..run.memory import KaguraCloudClient, MemoryClient
from .context import build_context_file
from .result import ReviewReport, ReviewStatus
from .reviewer import resolve_head, run_reviewer

_log = logging.getLogger(__name__)

REVIEW_STATUS_EXIT: dict[ReviewStatus, int] = {
    ReviewStatus.OK: 0,
    ReviewStatus.FAIL: 1,
    ReviewStatus.BLOCKED: 2,
}

_INFRA_RETURNCODES = {2, 3}


def review_pr(
    cfg: Config,
    target: str = "HEAD",
    *,
    base: str = "main",
    memory: MemoryClient | None = None,
    repo_root: Path | None = None,
) -> ReviewReport:
    mem = memory if memory is not None else KaguraCloudClient.from_config(cfg)
    root = repo_root if repo_root is not None else Path.cwd()
    started = time.monotonic()
    head = resolve_head(target)

    def _finish(**kw) -> ReviewReport:
        kw.setdefault("target", head)
        kw.setdefault("base", base)
        kw["duration_s"] = time.monotonic() - started
        return ReviewReport(**kw)

    # 1. recall — grounding for the reviewer context-file. A memory failure is
    # a hard FAIL (we surface it cleanly; reviewing ungrounded silently would
    # hide a broken memory layer).
    try:
        grounding = mem.load_pinned(cfg.context_id) + mem.recall(
            cfg.context_id, f"review {head} findings security correctness", k=5
        )
    except Exception as exc:  # noqa: BLE001
        _log.exception("review recall failed")
        return _finish(status=ReviewStatus.FAIL,
                       detail=f"memory recall failed: {type(exc).__name__}: {exc}")

    # 2. run reviewer (separate process). OSError = not on PATH; timeout and
    # infra exits are reported by ReviewerResult.
    with tempfile.TemporaryDirectory() as td:
        ctx = build_context_file(grounding, Path(td) / "grounding.md")
        out = Path(td) / "review.json"
        model = cfg.review.models[0] if cfg.review.models else None
        try:
            res = run_reviewer(
                base=base, head=head, repo=root, out=out,
                context_file=ctx, model=model,
            )
        except OSError as exc:
            _log.exception("review could not launch reviewer")
            return _finish(status=ReviewStatus.FAIL,
                           detail=f"could not launch kagura-code-reviewer: {exc}")
        report_path = str(out) if out.is_file() else None
        env = res.envelope

    # 3. interpret.
    if res.no_changes:
        return _finish(status=ReviewStatus.OK, verdict="green",
                       detail="no changes to review")
    if res.timed_out:
        return _finish(status=ReviewStatus.FAIL, detail="reviewer timed out")
    if res.returncode in _INFRA_RETURNCODES or not env.parsed:
        tail = (res.stderr or "").strip()[-200:]
        return _finish(status=ReviewStatus.FAIL,
                       detail=f"reviewer could not complete (exit {res.returncode}): {tail}")

    # 4. gate on the verdict (single gate definition, reused from run).
    decision = evaluate(env.verdict)
    n = env.summary.get("total", len(env.findings))
    incomplete = " (incomplete)" if env.incomplete else ""
    if decision.proceed:
        return _finish(
            status=ReviewStatus.OK, verdict=decision.verdict,
            summary=env.summary, findings=env.findings, report_path=report_path,
            detail=f"{decision.verdict}: {n} finding(s){incomplete}",
        )
    return _finish(
        status=ReviewStatus.BLOCKED, verdict=decision.verdict,
        summary=env.summary, findings=env.findings, report_path=report_path,
        detail=f"blocking verdict ({decision.verdict}): {n} finding(s){incomplete}",
        resume_hint=f"address the findings, then re-run `kagura-engineer review {target}`",
    )
