"""Result data model for the `review` command.

Mirrors `run/result.py`: a string-ish status enum + frozen dataclasses.
Unlike `run`, `review` is a single shot (launch reviewer → parse → gate),
so `ReviewReport.status` is set explicitly by the orchestrator rather than
derived from a phase list.

    OK       — reviewer completed; verdict green/yellow (or nothing to review)
    BLOCKED  — reviewer completed; verdict red (blocking findings) — resumable
    FAIL     — could not review (reviewer exit 2/3, not on PATH, timeout,
               unparseable envelope)

The CLI maps `status` to an exit code (0/1/2) via `REVIEW_STATUS_EXIT`.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..profile import ExecutionProfile


class ReviewStatus(enum.Enum):
    OK = "ok"
    BLOCKED = "blocked"
    FAIL = "fail"


@dataclass(frozen=True)
class Finding:
    dimension: str
    severity: str
    file: str
    line: int | None
    title: str


@dataclass(frozen=True)
class ReviewReport:
    target: str
    base: str
    verdict: str | None = None
    status: ReviewStatus = ReviewStatus.OK
    summary: dict[str, Any] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    detail: str = ""
    resume_hint: str | None = None
    report_path: str | None = None
    duration_s: float = 0.0
    # issue #70: the resolved ExecutionProfile — attached by the CLI,
    # serialised by render.to_json.
    profile: ExecutionProfile | None = None


@dataclass(frozen=True)
class ReviewLoopReport:
    """Plan 4b auto-fix loop result: an ordered list of per-iteration reviews
    (review → fix → re-review …) plus the loop's terminal status.

    `status` carries the same ReviewStatus meaning, decided by the loop:
      OK       — a fix made the review green/yellow (or it was already clean)
      BLOCKED  — still red after the fix budget (max_loops) was spent
      FAIL     — could not review, or a fixer invocation failed
    """
    target: str
    base: str
    iterations: list[ReviewReport] = field(default_factory=list)
    fixes_attempted: int = 0
    status: ReviewStatus = ReviewStatus.OK
    detail: str = ""
    resume_hint: str | None = None
    duration_s: float = 0.0
    # issue #70: see ReviewReport.profile.
    profile: ExecutionProfile | None = None

    @property
    def final(self) -> ReviewReport | None:
        return self.iterations[-1] if self.iterations else None
