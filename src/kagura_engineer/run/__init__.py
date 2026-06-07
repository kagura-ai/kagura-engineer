"""Plan 3 `run` — memory-grounded agent loop (idea→PR).

`run_idea` walks a fixed phase sequence and returns a `RunReport`:

    guard    → doctor blocking-check verification (no auto-setup)
    recall   → load_pinned + recall + get_state (grounding / resume)
    worktree → ensure run-<issue#> worktree (resumable)
    start    → claude -p /gh-issue-driven:start → gate
    ship     → claude -p /gh-issue-driven:ship  → gate → PR
    persist  → remember(savepoint) + set_state(done)   (skipped by --no-remember)

A red/unknown gate verdict halts with BLOCKED and a resume hint; a
non-zero claude exit is FAIL. Every external boundary (`run_all`,
`ensure_worktree`, `invoke_phase`, the `MemoryClient`) is imported at
module scope so tests can monkeypatch them.
"""
from __future__ import annotations

import time
from pathlib import Path

from ..config import Config
from ..doctor.registry import run_all
from .gate import evaluate
from .memory import KaguraCloudClient, MemoryClient
from .result import PhaseResult, RunReport, RunStatus
from .worktree import ensure_worktree
from .workflow import invoke_phase

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
    phases.append(PhaseResult("guard", RunStatus.OK, "all checks passed"))

    # 1. recall — grounding + resume point.
    grounding = mem.load_pinned(cfg.context_id) + mem.recall(
        cfg.context_id, f"issue {issue} implementation context", k=5
    )
    resumed = mem.get_state(cfg.context_id, _state_key(issue))
    detail = f"{len(grounding)} memories" + (" (resuming)" if resumed else "")
    phases.append(PhaseResult("recall", RunStatus.OK, detail))

    # 2. worktree.
    wt = ensure_worktree(root, issue)
    phases.append(PhaseResult("worktree", RunStatus.OK, str(wt)))

    # 3-4. act: start, then ship.
    pr_url = None
    for phase in _PHASES:
        inv = invoke_phase(phase, issue, wt, grounding)
        if inv.returncode != 0:
            tail = inv.stderr.strip()[-200:] if inv.stderr else ("timed out" if inv.timed_out else "")
            phases.append(PhaseResult(phase, RunStatus.FAIL, f"claude exited {inv.returncode}: {tail}"))
            return _finish(worktree=str(wt))
        decision = evaluate(inv.verdict)
        if not decision.proceed:
            mem.set_state(cfg.context_id, _state_key(issue), {"halted_at": phase, "verdict": decision.verdict})
            phases.append(PhaseResult(phase, RunStatus.BLOCKED, f"gate halt ({decision.verdict})", verdict=decision.verdict))
            return _finish(
                worktree=str(wt),
                resume_hint=f"review the {phase} gate, then re-run `kagura-engineer run {issue}`",
            )
        pr_url = inv.pr_url or pr_url
        phases.append(PhaseResult(phase, RunStatus.OK, f"{phase} ok", verdict=decision.verdict))

    # 5. persist.
    if not no_remember:
        mem.remember(
            cfg.context_id,
            summary=f"run #{issue} → PR {pr_url or '(no url)'}",
            content=f"kagura-engineer run drove issue #{issue} to {pr_url}",
            type="savepoint",
            tags=["repo:kagura-engineer", "run", f"issue:{issue}"],
        )
        mem.set_state(cfg.context_id, _state_key(issue), {"done": True, "pr_url": pr_url})
        phases.append(PhaseResult("persist", RunStatus.OK, "savepoint stored"))

    return _finish(pr_url=pr_url, worktree=str(wt))
