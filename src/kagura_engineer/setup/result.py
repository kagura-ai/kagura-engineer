"""Result data model for the `setup` command.

`setup` runs ordered provision steps (ensure_git, ensure_claude_login,
...), each of which can land in one of four terminal states:

    OK          — already healthy, or auto-fixed by this run
    SKIPPED     — out of scope for this profile / cfg (e.g. no models to pull)
    NEEDS_USER  — requires interactive input or an env var the user must set;
                  `--no-input` mode fails loudly on these
    FAIL        — hard error (network down, install command exited non-zero,
                  etc.); the user must investigate before `run` can proceed

`StepResult` mirrors the doctor `CheckResult` shape so the renderer can
share most of its layout. `SetupReport` aggregates the run into the four
buckets, which makes both the human-readable print path and the
machine-readable `--json` path trivial: the renderer just iterates the
buckets in the canonical order OK → SKIPPED → NEEDS_USER → FAIL.

Note: we deliberately do NOT reuse doctor `Status` (OK/WARN/FAIL).
`WARN` is a doctor concept (reachable but unauth'd), but for `setup`
that case is `OK` (we are not auth-checking) or `NEEDS_USER` (we are
asking the user to log in). Reusing the enum would conflate two
different decision boundaries.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field


class StepStatus(enum.Enum):
    OK = "ok"
    SKIPPED = "skipped"
    NEEDS_USER = "needs_user"
    FAIL = "fail"


@dataclass(frozen=True)
class StepResult:
    name: str
    status: StepStatus
    detail: str
    fix_hint: str | None = None
    duration_s: float = 0.0


@dataclass(frozen=True)
class SetupReport:
    ran: list[StepResult] = field(default_factory=list)
    skipped: list[StepResult] = field(default_factory=list)
    failed: list[StepResult] = field(default_factory=list)
    needs_user: list[StepResult] = field(default_factory=list)
    duration_s: float = 0.0

    @property
    def is_blocked(self) -> bool:
        # A blocked run = at least one FAIL or NEEDS_USER. `run` (Plan 3)
        # refuses to start under either of these conditions.
        return bool(self.failed) or bool(self.needs_user)
