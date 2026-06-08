"""Plan 3 `run` — memory-grounded agent loop (idea→PR).

`run_idea` walks a fixed phase sequence and returns a `RunReport`:

    guard     → doctor blocking-check verification (no auto-setup)
    recall    → load_pinned + recall + get_state (grounding / resume)
    worktree  → ensure run-<issue#> worktree (resumable)
    start     → claude -p /gh-issue-driven:start → gate (design)
    implement → claude -p TDD implementation → gate; a green phase that left no
                commit is a clean FAIL (nothing to ship — issue #9)
    ship      → claude -p /gh-issue-driven:ship  → gate → PR
    persist   → remember(savepoint) + set_state(done)   (skipped by --no-remember)

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
from typing import Callable

from ..config import Config
from ..doctor.registry import run_all
from .gate import evaluate
from .memory import MemoryClient, resolve_memory_client
from .result import PhaseResult, RunReport, RunStatus
from .worktree import WorktreeError, ensure_worktree
from .workflow import head_rev, invoke_phase

_log = logging.getLogger(__name__)

STATUS_EXIT: dict[RunStatus, int] = {
    RunStatus.OK: 0,
    RunStatus.FAIL: 1,
    RunStatus.BLOCKED: 2,
}

_PHASES = ("start", "implement", "ship")

# Soft cap on grounding lines injected into the phase prompt (pinned + recall +
# explore neighbours), so graph enrichment can't balloon the context.
_GROUNDING_CAP = 12

# issue #12: a long autonomous run is opaque — each `claude -p` phase captures
# its child's output, and the rich table renders only at the end, so an operator
# watching a multi-minute run (or a whole `goal` milestone) sees nothing until it
# finishes. A `ProgressSink` is an optional callback the loop calls as it
# advances — the CLI wires it to stdout, tests to a list. It carries ONLY the
# orchestrator's own phase markers; the verdict-marker parse still reads the full
# captured child stdout (`PhaseInvocation`), so the contract is untouched.
ProgressSink = Callable[[str], None]

# Phase-exit icons, mirroring run/render.py's table vocabulary.
_PROGRESS_ICON: dict[RunStatus, str] = {
    RunStatus.OK: "✅",
    RunStatus.BLOCKED: "⏸",
    RunStatus.FAIL: "❌",
}


def _state_key(issue: int) -> str:
    return f"run:{issue}"


def run_idea(
    cfg: Config,
    issue: int,
    *,
    no_remember: bool = False,
    unattended: bool = False,
    memory: MemoryClient | None = None,
    repo_root: Path | None = None,
    progress: ProgressSink | None = None,
) -> RunReport:
    mem = memory if memory is not None else resolve_memory_client(cfg)
    # We close ONLY a client we created — an injected one is the caller's (goal /
    # tests) to own. Every exit path returns via `_finish`, so closing there
    # covers success, halt, and FAIL alike.
    owns_mem = memory is None
    root = repo_root if repo_root is not None else Path.cwd()
    started = time.monotonic()
    phases: list[PhaseResult] = []

    # issue #12: single chokepoint — every recorded phase streams an exit line, so
    # the operator sees progress incrementally instead of a blank screen until the
    # final table. A sink failure must never sink the run, so emits are guarded.
    def _emit(line: str) -> None:
        if progress is None:
            return
        try:
            progress(line)
        except Exception:  # noqa: BLE001 — a broken sink must not fail the run
            _log.exception("run progress sink raised (non-fatal)")

    def _record(p: PhaseResult) -> None:
        phases.append(p)
        verdict = f" ({p.verdict})" if p.verdict else ""
        _emit(f"{_PROGRESS_ICON[p.status]} {p.name} {p.status.value}{verdict}")

    def _finish(*, pr_url=None, worktree=None, resume_hint=None) -> RunReport:
        # issue #14: a cloud client holds a persistent event loop + httpx client
        # that hangs the process at exit if never closed. Best-effort teardown,
        # guarded so it can never mask the run's result.
        if owns_mem and hasattr(mem, "close"):
            try:
                mem.close()
            except Exception:  # noqa: BLE001 — teardown is best-effort
                _log.exception("run memory client close failed (non-fatal)")
        return RunReport(
            issue=issue, phases=phases, pr_url=pr_url, worktree=worktree,
            resume_hint=resume_hint, duration_s=time.monotonic() - started,
        )

    # 0. guard — verify, do not auto-provision.
    blocking = [c for c in run_all(cfg) if c.is_blocking]
    if blocking:
        names = ", ".join(c.name for c in blocking)
        _record(PhaseResult("guard", RunStatus.BLOCKED, f"blocking checks failed: {names}"))
        return _finish(resume_hint="run `kagura-engineer setup` to fix the environment, then retry")
    _record(PhaseResult("guard", RunStatus.OK, "all blocking checks passed"))

    # 1. recall — grounding + resume point. Memory is core: a failure here
    # is a hard FAIL (we do not run ungrounded), surfaced cleanly not as a crash.
    recalled_ids: list[str] = []
    try:
        pinned = mem.load_pinned(cfg.context_id)
        recalled = mem.recall_detailed(
            cfg.context_id, f"issue {issue} implementation context", k=5
        )
        recalled_ids = [mid for mid, _ in recalled]
        grounding = pinned + [s for _, s in recalled]
        resumed = mem.get_state(cfg.context_id, _state_key(issue))
    except Exception as exc:  # noqa: BLE001 — convert any SDK leak to a FAIL phase
        _log.exception("run recall phase failed")
        _record(PhaseResult("recall", RunStatus.FAIL, f"memory recall failed: {type(exc).__name__}: {exc}"))
        return _finish()

    # 1a. expand grounding with graph neighbours of the top hit (recall→explore):
    # related past work the direct query missed. Best-effort — an explore failure
    # must NOT fail recall (a hard FAIL above); a soft cap bounds injected context.
    if recalled:
        try:
            seen = set(grounding)
            for _, summary in mem.explore(cfg.context_id, recalled[0][0], depth=1):
                if summary and summary not in seen and len(grounding) < _GROUNDING_CAP:
                    grounding.append(summary)
                    seen.add(summary)
        except Exception:  # noqa: BLE001 — graph enrichment is best-effort
            _log.exception("run explore enrichment failed (non-fatal)")

    detail = f"{len(grounding)} memories" + (" (resuming)" if resumed else "")
    _record(PhaseResult("recall", RunStatus.OK, detail))

    # 1b. cheap resume: a prior run already shipped this issue → no-op (skip
    # worktree + the two 30-min phases). Keeps `goal` re-runs cheap after a
    # mid-milestone halt instead of re-launching every already-shipped issue.
    if resumed and resumed.get("done"):
        _record(PhaseResult("act", RunStatus.OK, "already shipped (resumed)"))
        return _finish(pr_url=resumed.get("pr_url"))

    # 2. worktree.
    try:
        wt = ensure_worktree(root, issue)
    except (WorktreeError, OSError) as exc:
        _log.exception("run worktree phase failed")
        _record(PhaseResult("worktree", RunStatus.FAIL, f"worktree failed: {exc}"))
        return _finish()
    _record(PhaseResult("worktree", RunStatus.OK, str(wt)))

    # 3-5. act: start (design gate) → implement (TDD) → ship (PR gate).
    pr_url = None
    for phase in _PHASES:
        # issue #12: announce the phase BEFORE its multi-minute claude call, so a
        # stalled phase shows "▶ running" rather than a blank screen until timeout.
        _emit(f"▶ {phase} …")
        # issue #9: capture HEAD before implement so we can detect whether it
        # actually committed anything (a green design with no code leaves ship
        # nothing to package). None (unreadable) → degrade to skipping the check.
        head_before = head_rev(wt) if phase == "implement" else None
        try:
            inv = invoke_phase(phase, issue, wt, grounding, unattended=unattended,
                               mcp_config=cfg.memory_mcp_config)
        except OSError as exc:
            _log.exception("run %s phase failed to launch claude", phase)
            _record(PhaseResult(phase, RunStatus.FAIL, f"failed to launch claude: {exc}"))
            return _finish(worktree=str(wt))
        if inv.returncode != 0:
            if inv.timed_out:
                tail = "timed out"
            elif inv.stderr:
                tail = inv.stderr.strip()[-200:]
            else:
                tail = ""
            _record(PhaseResult(phase, RunStatus.FAIL, f"claude exited {inv.returncode}: {tail}"))
            return _finish(worktree=str(wt))
        decision = evaluate(inv.verdict)
        if not decision.proceed:
            # Resume marker is best-effort; a memory hiccup must not mask the halt.
            try:
                mem.set_state(cfg.context_id, _state_key(issue), {"halted_at": phase, "verdict": decision.verdict})
            except Exception:  # noqa: BLE001
                _log.exception("run halt set_state failed (non-fatal)")
            _record(PhaseResult(phase, RunStatus.BLOCKED, f"gate halt ({decision.verdict})", verdict=decision.verdict))
            return _finish(
                worktree=str(wt),
                resume_hint=f"review the {phase} gate, then re-run `kagura-engineer run {issue}`",
            )
        # issue #9: a green implement phase that left no commit is a clear,
        # named failure — not a confusing "ship red" downstream on an empty diff.
        if phase == "implement" and head_before is not None and head_rev(wt) == head_before:
            _record(PhaseResult(
                phase, RunStatus.FAIL,
                "implement produced no commit — design passed gate1 but no code "
                "was written/committed, so there is nothing to ship",
            ))
            return _finish(
                worktree=str(wt),
                resume_hint=f"implement issue #{issue} on the branch and commit, then re-run `kagura-engineer run {issue}`",
            )
        pr_url = inv.pr_url or pr_url
        # issue #18: a green ship that produced no PR URL did not actually push a
        # branch / open a PR — the run has NOT reached a PR. Reporting OK / exit 0
        # "PR reached" here is a false success (the same trap as #9's empty diff).
        # Cross-check the green verdict against the real artifact and FAIL if it's
        # missing, so `goal` and the exit code never claim a PR that doesn't exist.
        if phase == "ship" and not pr_url:
            _record(PhaseResult(
                phase, RunStatus.FAIL,
                "ship reported green but produced no PR URL — the branch was not "
                "pushed or no PR was opened, so the run did not reach a PR",
            ))
            return _finish(
                worktree=str(wt),
                resume_hint=f"check why ship did not open a PR, then re-run `kagura-engineer run {issue}`",
            )
        _record(PhaseResult(phase, RunStatus.OK, f"{phase} ok", verdict=decision.verdict))

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
            _record(PhaseResult("persist", RunStatus.OK, "savepoint stored"))
        except Exception as exc:  # noqa: BLE001 — PR is done; persist is best-effort
            _log.exception("run persist phase failed (non-fatal)")
            _record(PhaseResult("persist", RunStatus.OK, f"savepoint store failed (non-fatal): {type(exc).__name__}"))

        # Reinforce the memories that grounded this successful run — best-effort
        # and AFTER the savepoint/done-state, so a feedback hiccup can never cost
        # us the resume marker (recall→act→reinforce).
        for mid in recalled_ids:
            try:
                mem.feedback(cfg.context_id, mid)
            except Exception:  # noqa: BLE001 — reinforcement is best-effort
                _log.exception("run feedback failed (non-fatal)")

    return _finish(pr_url=pr_url, worktree=str(wt))
