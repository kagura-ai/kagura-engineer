"""`goal` — drive a whole GitHub milestone to PRs (v0.3 multi-issue).

`run_milestone` enumerates a milestone's open issues and runs the Plan 3
memory-grounded loop (`run_idea`) over each, in order:

    list issues → for each: run_idea → OK? continue : halt (HITL)

It auto-continues while issues reach OK (a PR), and halts the milestone at the
first issue that does not (BLOCKED gate / FAIL) — surfacing it for a human,
resumable by re-running (already-shipped issues are no-ops / resume cleanly).
This composes the tested actor loop; gh-issue-driven remains the per-issue
orchestrator. `list_milestone_issues` / `run_idea` are imported at module scope
so tests can monkeypatch them.
"""
from __future__ import annotations

import time
from pathlib import Path

from ..config import Config
from ..run import STATUS_EXIT, ProgressSink, run_idea
from ..run.memory import MemoryClient, resolve_memory_client
from ..run.result import RunStatus
from .issues import list_milestone_issues
from .result import GoalReport

# Same 0/1/2 mapping as `run` (OK / FAIL / BLOCKED).
GOAL_STATUS_EXIT = STATUS_EXIT


def run_milestone(
    cfg: Config,
    milestone: str,
    *,
    no_remember: bool = False,
    unattended: bool = False,
    memory: MemoryClient | None = None,
    repo_root: Path | None = None,
    progress: ProgressSink | None = None,
) -> GoalReport:
    started = time.monotonic()
    # The milestone owns ONE memory client across all its issues (passed to each
    # run_idea as injected, so run_idea never closes it). We close it here, in
    # `_finish`, so the cloud client's event loop doesn't hang the process at exit
    # (issue #14). Declared up-front (None) so the pre-`mem` early returns are safe.
    mem: MemoryClient | None = None
    owns_mem = False

    def _finish(**kw) -> GoalReport:
        if owns_mem and mem is not None and hasattr(mem, "close"):
            try:
                mem.close()
            except Exception:  # noqa: BLE001 — teardown is best-effort
                pass
        kw["duration_s"] = time.monotonic() - started
        return GoalReport(milestone=milestone, **kw)

    try:
        issues = list_milestone_issues(milestone)
    except Exception as exc:  # noqa: BLE001 — gh missing/error → clean FAIL
        return _finish(status=RunStatus.FAIL,
                       detail=f"could not list milestone issues: {type(exc).__name__}: {exc}")
    if not issues:
        return _finish(status=RunStatus.OK, detail=f"no open issues in milestone {milestone!r}")

    mem = memory if memory is not None else resolve_memory_client(cfg)
    owns_mem = memory is None
    reports = []
    for issue in issues:
        # issue #12: forward a per-issue progress sink so a whole-milestone run is
        # not silent — each run_idea line is prefixed with the issue it belongs to.
        issue_progress = (
            (lambda line, _i=issue: progress(f"#{_i} {line}"))
            if progress is not None else None
        )
        rep = run_idea(cfg, issue, no_remember=no_remember, unattended=unattended,
                       memory=mem, repo_root=repo_root, progress=issue_progress)
        reports.append(rep)
        if rep.status is not RunStatus.OK:
            # Halt the milestone at the first issue needing a human; resumable.
            return _finish(
                issues=reports, status=rep.status,
                detail=f"halted at issue #{issue} ({rep.status.value}) — {len(reports) - 1}/{len(issues)} shipped",
                resume_hint=f"resolve issue #{issue} (see its report), then re-run "
                            f"`kagura-engineer goal {milestone}`",
            )
    return _finish(issues=reports, status=RunStatus.OK,
                   detail=f"all {len(issues)} issue(s) shipped")
