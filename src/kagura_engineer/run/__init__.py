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
import os
import re
import time
from pathlib import Path
from typing import Callable

from ..config import Config, ConfigError
from ..doctor.registry import run_all
from .brain_select import select_brain
from .gate import evaluate
from .memory import MemoryClient, resolve_memory_client
from .result import STATUS_ICON, PhaseResult, RunReport, RunStatus
from .worktree import WorktreeError, ensure_worktree
from .workflow import head_rev, invoke_phase, persist_phase_stdout

_log = logging.getLogger(__name__)

STATUS_EXIT: dict[RunStatus, int] = {
    RunStatus.OK: 0,
    RunStatus.FAIL: 1,
    RunStatus.BLOCKED: 2,
}

_PHASES = ("start", "implement", "ship")

# issue #12 progress-stream glyphs reuse the table renderer's single source of
# truth (run/result.STATUS_ICON) so a streamed phase marker can never drift from
# the final RunReport table.
_PROGRESS_ICON = STATUS_ICON

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

# issue #19: headless `claude -p` prints a fatal auth failure to STDOUT (not
# stderr) and exits 1 — e.g. "Invalid API key · Fix external API key" — when a
# stale/invalid ANTHROPIC_API_KEY in the environment overrides the operator's
# logged-in (OAuth) session. We do NOT silently scrub the key (that would break
# legitimate API-key auth, e.g. CI); instead we detect the signature and hand the
# operator the exact, safe remedy.
# Match claude's FULL auth-failure signature ("Invalid API key · Fix external API
# key") — both phrases, in order — not the standalone "invalid api key", which can
# appear in unrelated model output (e.g. a doc discussing API-key handling) and
# would wrongly blame ANTHROPIC_API_KEY. DOTALL so the separator can be anything.
_AUTH_FAIL_RE = re.compile(
    r"invalid api key.*?fix external api key", re.IGNORECASE | re.DOTALL
)


def _auth_failure_hint(stdout: str, stderr: str, issue: int) -> str | None:
    if _AUTH_FAIL_RE.search(f"{stdout}\n{stderr}"):
        return (
            "headless claude reported an API-key error. This is usually a "
            "stale/invalid ANTHROPIC_API_KEY in the environment overriding your "
            "logged-in (OAuth) session — if it is set, unset it and re-run: "
            f"`env -u ANTHROPIC_API_KEY kagura-engineer run {issue}`"
        )
    return None


def _state_key(issue: int, label: str | None = None) -> str:
    return f"run:{issue}" if label is None else f"run:{issue}:{label}"


def run_idea(
    cfg: Config,
    issue: int,
    *,
    no_remember: bool = False,
    unattended: bool = False,
    ground: bool = True,
    run_label: str | None = None,
    memory: MemoryClient | None = None,
    repo_root: Path | None = None,
    progress: ProgressSink | None = None,
) -> RunReport:
    # issue #57: `ground` is the A/B control-arm switch. ground=True (default) is
    # the normal grounded loop — pinned + recall + graph-expanded memory injected
    # into every phase prompt. ground=False is the control arm: the loop runs
    # byte-for-byte identically EXCEPT no grounding is pulled or injected
    # (load_pinned / recall / explore are skipped, and nothing is reinforced). The
    # resume marker (get_state/set_state) is part of the loop, not grounding, so it
    # still works — this keeps the two arms identical apart from the one variable
    # under test: memory grounding. The eval harness (kagura_engineer.eval) runs
    # the same issue set in both arms and compares objective PR-quality signals.
    #
    # `run_label` (issue #57) isolates the two arms of the SAME issue from each
    # other: it suffixes the worktree (run-<issue>-<label>), the resume-state key
    # (run:<issue>:<label>), and the start phase's branch (--branch=run-<issue>-
    # <label>). Without it both arms would key off the issue number alone and the
    # control arm would resume / build on the grounded arm's worktree+branch+PR,
    # invalidating the A/B. None (normal runs) keeps the historical issue-only
    # naming untouched.
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

    # Failover: replay any writes buffered during a prior Cloud outage before we
    # start. getattr-guarded (only FailoverMemoryClient has drain), and fully
    # best-effort — a drain failure must never fail the run; records stay in the
    # WAL for the next attempt.
    drainer = getattr(mem, "drain", None)
    if drainer is not None:
        try:
            drainer()
        except Exception:  # noqa: BLE001 — drain is best-effort
            _log.exception("run failover drain failed (non-fatal)")

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
    recalled: list[tuple[str, str]] = []
    grounding: list[str] = []
    try:
        # Control arm (ground=False): pull NO grounding — skip pinned/recall
        # entirely so the loop runs ungrounded. Resume state is read either way.
        if ground:
            pinned = mem.load_pinned(cfg.context_id)
            recalled = mem.recall_detailed(
                cfg.context_id, f"issue {issue} implementation context", k=5
            )
            recalled_ids = [mid for mid, _ in recalled]
            grounding = pinned + [s for _, s in recalled]
        resumed = mem.get_state(cfg.context_id, _state_key(issue, run_label))
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

    if ground:
        detail = f"{len(grounding)} memories" + (" (resuming)" if resumed else "")
    else:
        # Control arm: name the disabled-grounding state so the report is honest
        # about which A/B arm produced it.
        detail = "grounding off (control arm)" + (" (resuming)" if resumed else "")
    _record(PhaseResult("recall", RunStatus.OK, detail))

    # 1b. cheap resume: a prior run already shipped this issue → no-op (skip
    # worktree + the two 30-min phases). Keeps `goal` re-runs cheap after a
    # mid-milestone halt instead of re-launching every already-shipped issue.
    if resumed and resumed.get("done"):
        _record(PhaseResult("act", RunStatus.OK, "already shipped (resumed)"))
        return _finish(pr_url=resumed.get("pr_url"))

    # 2. worktree.
    try:
        wt = ensure_worktree(root, issue, label=run_label)
    except (WorktreeError, OSError) as exc:
        _log.exception("run worktree phase failed")
        _record(PhaseResult("worktree", RunStatus.FAIL, f"worktree failed: {exc}"))
        return _finish()
    _record(PhaseResult("worktree", RunStatus.OK, str(wt)))

    # 3-5. act: start (design gate) → implement (TDD) → ship (PR gate).
    try:
        brain_call = select_brain(cfg, os.environ)
    except ConfigError as exc:
        _record(PhaseResult("brain", RunStatus.FAIL, f"backend config error: {exc}"))
        return _finish(worktree=str(wt))
    pr_url = None
    # issue #57: when this is an isolated arm, force the start phase onto an
    # arm-specific branch (gh-issue-driven:start honours --branch=<name>) so the
    # two arms of one issue land on distinct branches/PRs instead of colliding.
    # Matches the worktree name for traceability. None → gh-issue-driven derives
    # its normal typed branch (normal runs unchanged).
    branch_override = f"run-{issue}-{run_label}" if run_label else None
    for phase in _PHASES:
        # issue #12: announce the phase BEFORE its multi-minute brain call, so a
        # stalled phase shows "▶ running" rather than a blank screen until timeout.
        _emit(f"▶ {phase} …")
        # issue #9: capture HEAD before implement so we can detect whether it
        # actually committed anything (a green design with no code leaves ship
        # nothing to package). None (unreadable) → degrade to skipping the check.
        head_before = head_rev(wt) if phase == "implement" else None
        try:
            inv = invoke_phase(phase, issue, wt, grounding, brain_call=brain_call,
                               unattended=unattended,
                               mcp_config=cfg.resolve_mcp_config(root),
                               # only start CREATES the branch; implement/ship
                               # follow the worktree's current branch.
                               branch_override=branch_override if phase == "start" else None)
        except OSError as exc:
            _log.exception("run %s phase failed to launch %s", phase, brain_call.backend)
            _record(PhaseResult(
                phase, RunStatus.FAIL,
                f"failed to launch {brain_call.backend}: {exc}",
            ))
            return _finish(worktree=str(wt))
        if inv.returncode != 0:
            if inv.timed_out:
                tail, hint = "timed out", None
            else:
                # issue #19: the backend CLI surfaces some fatal errors (e.g.
                # claude's "Invalid API key") on STDOUT, not stderr — fall back to
                # stdout so the failure is never an opaque "<backend> exited 1:"
                # with an empty tail. If BOTH streams are empty, say so explicitly.
                tail = (inv.stderr.strip() or inv.stdout.strip())[-200:] \
                    or "(no output captured)"
                hint = _auth_failure_hint(inv.stdout, inv.stderr, issue)
            _record(PhaseResult(
                phase, RunStatus.FAIL,
                f"{brain_call.backend} exited {inv.returncode}: {tail}",
            ))
            return _finish(worktree=str(wt), resume_hint=hint)
        decision = evaluate(inv.verdict)
        if not decision.proceed:
            # Resume marker is best-effort; a memory hiccup must not mask the halt.
            try:
                mem.set_state(cfg.context_id, _state_key(issue, run_label), {"halted_at": phase, "verdict": decision.verdict})
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
        # Cross-check THIS phase's artifact (`inv.pr_url`, not the accumulated
        # `pr_url`) so a stray URL from an earlier phase can't mask a ship that
        # produced none, and FAIL if it's missing.
        if phase == "ship" and not inv.pr_url:
            # issue #38: this is the silent failure mode — ship went green yet
            # skipped push / `gh pr create`, and `run --json` ate the child's
            # reasoning. Persist that captured stdout to the worktree so the skip
            # is diagnosable without a re-run, and point the operator at it.
            detail = (
                "ship reported green but produced no PR URL — the branch was not "
                "pushed or no PR was opened, so the run did not reach a PR"
            )
            log_path = persist_phase_stdout(wt, inv)
            if log_path is not None:
                detail += f"; ship stdout saved to {log_path} for diagnosis"
            _record(PhaseResult(phase, RunStatus.FAIL, detail))
            return _finish(
                worktree=str(wt),
                resume_hint=f"check why ship did not open a PR (see {log_path or 'the ship log'}), then re-run `kagura-engineer run {issue}`",
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
            mem.set_state(cfg.context_id, _state_key(issue, run_label), {"done": True, "pr_url": pr_url})
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
