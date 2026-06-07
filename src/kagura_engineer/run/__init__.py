"""Plan 3 `run` — memory-grounded agent loop (idea→PR).

`run_idea` walks a fixed phase sequence and returns a `RunReport`:

    guard    → doctor blocking-check verification (no auto-setup)
    recall   → load_pinned + recall + get_state (grounding / resume)
    worktree → ensure run-<issue#> worktree (resumable)
    start    → claude -p /gh-issue-driven:start → gate
    ship     → claude -p /gh-issue-driven:ship  → gate → PR
    persist  → remember(savepoint) + set_state(done)   (skipped by --no-remember)

A red/unknown gate verdict halts with BLOCKED and a resume hint; a
non-zero claude exit is FAIL. External calls (worktree, memory SDK, claude launch) are
wrapped in try/except so an infrastructure error returns a FAIL
RunReport with a clean exit code instead of a traceback — the same
isolation invariant setup.run_plan and doctor.run_all enforce. Every
external boundary (`run_all`, `ensure_worktree`, `invoke_phase`, the
`MemoryClient`) is imported at module scope so tests can monkeypatch them.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from ..config import Config
from ..doctor.registry import run_all
from .gate import evaluate
from .memory import KaguraCloudClient, MemoryClient
from .result import PhaseResult, RunReport, RunStatus
from .worktree import WorktreeError, ensure_worktree
from .workflow import invoke_phase

_log = logging.getLogger(__name__)

STATUS_EXIT: dict[RunStatus, int] = {
    RunStatus.OK: 0,
    RunStatus.FAIL: 1,
    RunStatus.BLOCKED: 2,
}

_PHASES = ("start", "ship")


def _state_key(issue: int) -> str:
    return f"run:{issue}"


def run_idea(
    cfg: Config,
    issue: int,
    *,
    no_remember: bool = False,
    memory: MemoryClient | None = None,
    repo_root: Path | None = None,
) -> RunReport:
    mem = memory if memory is not None else KaguraCloudClient.from_config(cfg)
    root = repo_root if repo_root is not None else Path.cwd()
    started = time.monotonic()
    phases: list[PhaseResult] = []

    def _finish(*, pr_url=None, worktree=None, resume_hint=None) -> RunReport:
        return RunReport(
            issue=issue, phases=phases, pr_url=pr_url, worktree=worktree,
            resume_hint=resume_hint, duration_s=time.monotonic() - started,
        )

    # 0. guard — verify, do not auto-provision.
    blocking = [c for c in run_all(cfg) if c.is_blocking]
    if blocking:
        names = ", ".join(c.name for c in blocking)
        phases.append(PhaseResult("guard", RunStatus.BLOCKED, f"blocking checks failed: {names}"))
        return _finish(resume_hint="run `kagura-engineer setup` to fix the environment, then retry")
    phases.append(PhaseResult("guard", RunStatus.OK, "all blocking checks passed"))

    # 1. recall — grounding + resume point. Memory is core: a failure here
    # is a hard FAIL (we do not run ungrounded), surfaced cleanly not as a crash.
    try:
        grounding = mem.load_pinned(cfg.context_id) + mem.recall(
            cfg.context_id, f"issue {issue} implementation context", k=5
        )
        resumed = mem.get_state(cfg.context_id, _state_key(issue))
    except Exception as exc:  # noqa: BLE001 — convert any SDK leak to a FAIL phase
        _log.exception("run recall phase failed")
        phases.append(PhaseResult("recall", RunStatus.FAIL, f"memory recall failed: {type(exc).__name__}: {exc}"))
        return _finish()
    detail = f"{len(grounding)} memories" + (" (resuming)" if resumed else "")
    phases.append(PhaseResult("recall", RunStatus.OK, detail))

    # 2. worktree.
    try:
        wt = ensure_worktree(root, issue)
    except (WorktreeError, OSError) as exc:
        _log.exception("run worktree phase failed")
        phases.append(PhaseResult("worktree", RunStatus.FAIL, f"worktree failed: {exc}"))
        return _finish()
    phases.append(PhaseResult("worktree", RunStatus.OK, str(wt)))

    # 3-4. act: start, then ship.
    pr_url = None
    for phase in _PHASES:
        try:
            inv = invoke_phase(phase, issue, wt, grounding)
        except OSError as exc:
            _log.exception("run %s phase failed to launch claude", phase)
            phases.append(PhaseResult(phase, RunStatus.FAIL, f"failed to launch claude: {exc}"))
            return _finish(worktree=str(wt))
        if inv.returncode != 0:
            if inv.timed_out:
                tail = "timed out"
            elif inv.stderr:
                tail = inv.stderr.strip()[-200:]
            else:
                tail = ""
            phases.append(PhaseResult(phase, RunStatus.FAIL, f"claude exited {inv.returncode}: {tail}"))
            return _finish(worktree=str(wt))
        decision = evaluate(inv.verdict)
        if not decision.proceed:
            # Resume marker is best-effort; a memory hiccup must not mask the halt.
            try:
                mem.set_state(cfg.context_id, _state_key(issue), {"halted_at": phase, "verdict": decision.verdict})
            except Exception:  # noqa: BLE001
                _log.exception("run halt set_state failed (non-fatal)")
            phases.append(PhaseResult(phase, RunStatus.BLOCKED, f"gate halt ({decision.verdict})", verdict=decision.verdict))
            return _finish(
                worktree=str(wt),
                resume_hint=f"review the {phase} gate, then re-run `kagura-engineer run {issue}`",
            )
        pr_url = inv.pr_url or pr_url
        phases.append(PhaseResult(phase, RunStatus.OK, f"{phase} ok", verdict=decision.verdict))

    # 5. persist. The PR already exists, so a persist failure is non-fatal:
    # the run still succeeded; we record the lost savepoint in the detail.
    if not no_remember:
        try:
            mem.remember(
                cfg.context_id,
                summary=f"run #{issue} → PR {pr_url or '(no url)'}",
                content=f"kagura-engineer run drove issue #{issue} to {pr_url}",
                type="savepoint",
                tags=[f"repo:{root.name}", "run", f"issue:{issue}"],
            )
            mem.set_state(cfg.context_id, _state_key(issue), {"done": True, "pr_url": pr_url})
            phases.append(PhaseResult("persist", RunStatus.OK, "savepoint stored"))
        except Exception as exc:  # noqa: BLE001 — PR is done; persist is best-effort
            _log.exception("run persist phase failed (non-fatal)")
            phases.append(PhaseResult("persist", RunStatus.OK, f"savepoint store failed (non-fatal): {type(exc).__name__}"))

    return _finish(pr_url=pr_url, worktree=str(wt))
