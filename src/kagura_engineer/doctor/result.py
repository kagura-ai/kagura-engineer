from __future__ import annotations

import enum
from dataclasses import dataclass


class Status(enum.Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    detail: str
    fix_hint: str | None = None

    @property
    def is_blocking(self) -> bool:
        return self.status is Status.FAIL
