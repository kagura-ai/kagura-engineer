"""Result data model for the `run` command.

Mirrors `setup/result.py` and `doctor/result.py`: frozen dataclasses,
a string Enum, and an aggregate with a derived `status`. `run` walks a
fixed phase sequence (guard → recall → worktree → start → ship →
persist); each phase lands in one of three terminal states:

    OK       — phase completed
    BLOCKED  — a gate halted the run (red/unknown verdict) or a blocking
               guard check failed; the run is resumable
    FAIL     — hard error (claude exited non-zero, timeout, SDK auth)

`RunReport.status` is the worst phase status (FAIL > BLOCKED > OK); the
CLI maps it to an exit code (0/1/2) via `STATUS_EXIT` in __init__.py.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field


class RunStatus(enum.Enum):
    OK = "ok"
    BLOCKED = "blocked"
    FAIL = "fail"


_WORST = {RunStatus.OK: 0, RunStatus.BLOCKED: 1, RunStatus.FAIL: 2}


@dataclass(frozen=True)
class PhaseResult:
    name: str
    status: RunStatus
    detail: str
    verdict: str | None = None
    duration_s: float = 0.0


@dataclass(frozen=True)
class RunReport:
    issue: int
    phases: list[PhaseResult] = field(default_factory=list)
    pr_url: str | None = None
    worktree: str | None = None
    resume_hint: str | None = None
    duration_s: float = 0.0

    @property
    def status(self) -> RunStatus:
        if not self.phases:
            return RunStatus.OK
        return max(self.phases, key=lambda p: _WORST[p.status]).status
