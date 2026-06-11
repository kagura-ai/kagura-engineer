"""Result model for the `goal` command (drive a milestone to PRs).

Reuses `run`'s `RunStatus`/`RunReport`: a `GoalReport` is an ordered list of
per-issue `RunReport`s plus the milestone's terminal status (the status of the
first issue that did not reach OK, or OK if every issue produced a PR).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..run.result import RunReport, RunStatus

if TYPE_CHECKING:
    from ..profile import ExecutionProfile


@dataclass(frozen=True)
class GoalReport:
    milestone: str
    issues: list[RunReport] = field(default_factory=list)
    status: RunStatus = RunStatus.OK
    detail: str = ""
    resume_hint: str | None = None
    duration_s: float = 0.0
    # issue #70: the resolved ExecutionProfile (per-config, shared by every
    # issue in the milestone) — attached by the CLI, serialised by to_json.
    profile: ExecutionProfile | None = None

    @property
    def completed(self) -> int:
        return sum(1 for r in self.issues if r.status is RunStatus.OK)
