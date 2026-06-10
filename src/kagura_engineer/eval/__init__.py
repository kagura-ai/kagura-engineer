"""`eval` — A/B measurement of memory-grounded uplift (issue #57, moat lever M3).

The moat claim — *"a memory-grounded coding agent measurably produces better PRs
because it remembers past work"* — is here turned from an assertion into a
measurement. `run_ab_eval` drives the **same** fixed issue set through two arms:

    grounded (A) — the normal run loop (recall + pinned + graph-expanded memory)
    control  (B) — the identical loop with grounding disabled (run_idea ground=False)

and compares them on objective signals already in the pipeline (gate verdicts,
PR-reached rate, and — when a `review_fn` is supplied — review findings
count/severity and re-fix-loop iterations).

The orchestrator is execution-agnostic: it takes a `run_fn(issue, *, ground)` and
an optional `review_fn(run_report, grounded)`, so the comparison is unit-tested
with fakes and the CLI wires the real `run_idea` / auto-review loop. This keeps
the expensive, repo-mutating run behind an injected boundary — the same isolation
the rest of the harness uses.
"""
from __future__ import annotations

import time
from typing import Callable

from ..review.result import ReviewLoopReport
from ..run.result import RunReport
from .metrics import build_arm_run
from .result import EvalReport

# A run of one issue in one arm. `ground=True` is the grounded arm; `ground=False`
# the control arm (see run_idea's `ground` switch).
RunFn = Callable[..., RunReport]
# Optional post-run review of an arm's PR/branch → the auto-fix loop report whose
# findings + iteration count feed the PR-quality signals. `grounded` is passed so
# a caller can label/scope the review per arm if needed.
ReviewFn = Callable[[RunReport, bool], ReviewLoopReport | None]

# A progress sink, mirroring run/goal: the eval calls it with a human line as each
# arm completes, so a long multi-issue A/B is not a blank screen.
ProgressSink = Callable[[str], None]


def run_ab_eval(
    issues: list[int],
    run_fn: RunFn,
    *,
    review_fn: ReviewFn | None = None,
    progress: ProgressSink | None = None,
) -> EvalReport:
    """Run each issue through the grounded and control arms; return the A/B report.

    For each issue the grounded arm runs first, then the control arm — both via the
    injected `run_fn`. When `review_fn` is given, each arm's run report is reviewed
    and the findings/iteration signals are folded into its `ArmRun`.
    """
    started = time.monotonic()
    grounded_runs = []
    control_runs = []

    def _emit(line: str) -> None:
        if progress is None:
            return
        try:
            progress(line)
        except Exception:  # noqa: BLE001 — a broken sink must not fail the eval
            pass

    for issue in issues:
        # True = grounded arm, False = control arm. Grounded first so the table
        # reads A-then-B and a partial run still has the grounded baseline.
        for grounded in (True, False):
            arm_name = "grounded" if grounded else "control"
            _emit(f"▶ #{issue} {arm_name} arm …")
            run_report = run_fn(issue, ground=grounded)
            review_report = review_fn(run_report, grounded) if review_fn else None
            arm = build_arm_run(
                issue, grounded=grounded,
                run_report=run_report, review_report=review_report,
            )
            (grounded_runs if grounded else control_runs).append(arm)
            _emit(f"  #{issue} {arm_name}: {arm.outcome}")

    return EvalReport(
        issues=list(issues),
        grounded_runs=grounded_runs,
        control_runs=control_runs,
        duration_s=time.monotonic() - started,
    )
