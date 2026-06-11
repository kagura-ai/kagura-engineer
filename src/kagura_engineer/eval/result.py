"""Result model for the `eval` command (issue #57 — moat lever M3).

The A/B eval measures whether memory grounding produces measurably better PRs.
It runs the *same* fixed issue set in two arms:

    grounded (A) — the normal run loop with recall + pinned + graph-expanded memory
    control  (B) — the identical loop with grounding disabled (run_idea ground=False)

Each (issue, arm) is distilled to objective signals already in the pipeline
(`ArmRun`); the per-arm aggregate is an `ArmStats`; the two arms' delta is an
`Uplift`. `EvalReport` bundles the raw runs and derives stats + uplift on demand,
so the comparison logic lives in one place and is unit-tested without ever
launching a real run.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..run.result import RunStatus

if TYPE_CHECKING:
    from ..profile import ExecutionProfile


@dataclass(frozen=True)
class ArmRun:
    """One arm's outcome for one issue, reduced to objective signals.

    `outcome` is the single per-issue gate label: ``green``/``yellow`` (the run
    reached a PR; yellow = some gate was a soft pass), ``red``/``unknown`` (a gate
    halted it), or ``fail`` (hard error). The review signals are ``None`` when the
    eval ran run-only (no `review_fn`).
    """
    issue: int
    grounded: bool
    status: RunStatus
    pr_reached: bool
    outcome: str
    gate_verdicts: tuple[str, ...] = ()
    findings_total: int | None = None
    findings_blocking: int | None = None
    fix_iterations: int | None = None


@dataclass(frozen=True)
class ArmStats:
    """Aggregate of one arm across the whole issue set."""
    grounded: bool
    n: int
    pr_reached: int
    green: int
    yellow: int
    red: int
    unknown: int
    failed: int
    # Review aggregates (sums) over only the runs that actually had a review.
    reviewed: int
    findings_total_sum: int
    findings_blocking_sum: int
    fix_iterations_sum: int

    @classmethod
    def from_runs(cls, runs: list[ArmRun], *, grounded: bool) -> "ArmStats":
        reviewed = [r for r in runs if r.findings_total is not None]
        return cls(
            grounded=grounded,
            n=len(runs),
            pr_reached=sum(1 for r in runs if r.pr_reached),
            green=sum(1 for r in runs if r.outcome == "green"),
            yellow=sum(1 for r in runs if r.outcome == "yellow"),
            red=sum(1 for r in runs if r.outcome == "red"),
            unknown=sum(1 for r in runs if r.outcome == "unknown"),
            failed=sum(1 for r in runs if r.outcome == "fail"),
            reviewed=len(reviewed),
            findings_total_sum=sum(r.findings_total or 0 for r in reviewed),
            findings_blocking_sum=sum(r.findings_blocking or 0 for r in reviewed),
            fix_iterations_sum=sum(r.fix_iterations or 0 for r in reviewed),
        )

    def _rate(self, count: int) -> float:
        return count / self.n if self.n else 0.0

    @property
    def pr_rate(self) -> float:
        return self._rate(self.pr_reached)

    @property
    def green_rate(self) -> float:
        return self._rate(self.green)

    @property
    def mean_findings(self) -> float | None:
        return self.findings_total_sum / self.reviewed if self.reviewed else None

    @property
    def mean_blocking(self) -> float | None:
        return self.findings_blocking_sum / self.reviewed if self.reviewed else None

    @property
    def mean_fix_iterations(self) -> float | None:
        return self.fix_iterations_sum / self.reviewed if self.reviewed else None


@dataclass(frozen=True)
class Uplift:
    """The raw grounded-minus-control delta on each signal, plus a verdict.

    Every delta is ``grounded - control``. The *good direction* differs per metric:
      * pr_rate_delta / green_rate_delta — higher grounded rate is better (>0 good).
      * findings / blocking / fix-iterations — fewer is better (<0 good).
    The review deltas are ``None`` when neither arm produced a review. `verdict`
    folds the per-metric good directions into one word (see `_verdict`).
    """
    pr_rate_delta: float
    green_rate_delta: float
    mean_findings_delta: float | None
    mean_blocking_delta: float | None
    mean_fix_iterations_delta: float | None
    verdict: str


def _delta(grounded: float | None, control: float | None) -> float | None:
    if grounded is None or control is None:
        return None
    return grounded - control


def _verdict(
    higher_better: list[float],
    lower_better: list[float | None],
) -> str:
    """Net direction across the available signals, accounting for each metric's
    good direction. >0 net = grounding improved; <0 = regressed; 0 = neutral."""
    good = sum(1 for d in higher_better if d > 0)
    bad = sum(1 for d in higher_better if d < 0)
    for d in lower_better:
        if d is None:
            continue
        if d < 0:      # fewer findings/fixes under grounding = good
            good += 1
        elif d > 0:
            bad += 1
    if good == 0 and bad == 0:
        return "neutral"
    if good > bad:
        return "improved"
    if bad > good:
        return "regressed"
    return "neutral"


@dataclass(frozen=True)
class EvalReport:
    issues: list[int] = field(default_factory=list)
    grounded_runs: list[ArmRun] = field(default_factory=list)
    control_runs: list[ArmRun] = field(default_factory=list)
    duration_s: float = 0.0
    # issue #70: the resolved ExecutionProfile both arms ran with — attached
    # by the CLI, serialised by render.to_json.
    profile: ExecutionProfile | None = None

    @property
    def grounded_stats(self) -> ArmStats:
        return ArmStats.from_runs(self.grounded_runs, grounded=True)

    @property
    def control_stats(self) -> ArmStats:
        return ArmStats.from_runs(self.control_runs, grounded=False)

    @property
    def uplift(self) -> Uplift:
        if not self.issues:
            return Uplift(0.0, 0.0, None, None, None, "inconclusive")
        g, c = self.grounded_stats, self.control_stats
        pr_delta = g.pr_rate - c.pr_rate
        green_delta = g.green_rate - c.green_rate
        findings_delta = _delta(g.mean_findings, c.mean_findings)
        blocking_delta = _delta(g.mean_blocking, c.mean_blocking)
        fix_delta = _delta(g.mean_fix_iterations, c.mean_fix_iterations)
        verdict = _verdict(
            higher_better=[pr_delta, green_delta],
            lower_better=[findings_delta, blocking_delta, fix_delta],
        )
        return Uplift(pr_delta, green_delta, findings_delta, blocking_delta, fix_delta, verdict)
