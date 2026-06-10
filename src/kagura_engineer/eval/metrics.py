"""Extract objective PR-quality signals from a run (+ optional review).

Pure functions — no I/O. They read a `RunReport` (and an optional
`ReviewLoopReport` from the auto-review/fix loop) and reduce them to the signals
the A/B eval compares. Keeping extraction here (not in the orchestrator) means
the metric definitions are unit-tested directly against hand-built reports.
"""
from __future__ import annotations

from ..run.result import RunReport, RunStatus
from ..review.result import ReviewLoopReport
from .result import ArmRun

# Same blocking-severity set the auto-fix loop uses to decide what to fix
# (review/loop.py) — kept in sync so "blocking findings" means the same thing in
# the eval table as it does in the fix loop.
_BLOCKING_SEVERITIES = {"HIGH", "CRITICAL"}


def pr_reached(report: RunReport) -> bool:
    """True only when the run reached a real PR — OK status with a PR URL.

    `run_idea` already enforces (issue #18) that a green ship without a PR URL is
    a FAIL, so OK implies a URL; the explicit URL check makes that contract
    visible here rather than assumed."""
    return report.status is RunStatus.OK and report.pr_url is not None


def gate_verdicts(report: RunReport) -> tuple[str, ...]:
    """Every recorded gate verdict, in phase order (start/implement/ship)."""
    return tuple(p.verdict for p in report.phases if p.verdict)


def outcome_verdict(report: RunReport) -> str:
    """The single per-issue gate label.

    OK      → ``green``, or ``yellow`` if any gate was a soft (yellow) pass —
              a yellow anywhere downgrades the issue's label, since the run still
              shipped but not cleanly.
    BLOCKED → the halting verdict carried on the final phase (``red``/``unknown``).
              A guard-block (or any halt with no verdict on the final phase) has
              no halting verdict, so it maps to ``unknown`` — never the literal
              ``"blocked"``, which is outside the outcome vocabulary
              (green/yellow/red/unknown/fail) and would be silently dropped by
              ``ArmStats.from_runs``.
    FAIL    → ``fail``.
    """
    if report.status is RunStatus.FAIL:
        return "fail"
    if report.status is RunStatus.BLOCKED:
        last = report.phases[-1] if report.phases else None
        return last.verdict if (last and last.verdict) else "unknown"
    return "yellow" if "yellow" in gate_verdicts(report) else "green"


def _review_counts(review: ReviewLoopReport) -> tuple[int, int]:
    """(findings_total, findings_blocking) from the loop's final review.

    Prefers the reviewer's own summary counts (authoritative — they reflect the
    full envelope, not the actor-side display slice); falls back to deriving them
    from the findings list when the summary omits them."""
    final = review.final
    if final is None:
        return 0, 0
    summary = final.summary or {}
    total = summary.get("total")
    if not isinstance(total, int):
        total = len(final.findings)
    blocking = summary.get("blocking")
    if not isinstance(blocking, int):
        blocking = sum(
            1 for f in final.findings if f.severity.upper() in _BLOCKING_SEVERITIES
        )
    return total, blocking


def build_arm_run(
    issue: int,
    *,
    grounded: bool,
    run_report: RunReport,
    review_report: ReviewLoopReport | None = None,
) -> ArmRun:
    """Distil one (issue, arm) run — and its optional review — into an `ArmRun`."""
    findings_total = findings_blocking = fix_iterations = None
    if review_report is not None:
        findings_total, findings_blocking = _review_counts(review_report)
        fix_iterations = review_report.fixes_attempted
    return ArmRun(
        issue=issue,
        grounded=grounded,
        status=run_report.status,
        pr_reached=pr_reached(run_report),
        outcome=outcome_verdict(run_report),
        gate_verdicts=gate_verdicts(run_report),
        findings_total=findings_total,
        findings_blocking=findings_blocking,
        fix_iterations=fix_iterations,
    )
