from __future__ import annotations

from ..config import Config
from . import checks
from .result import CheckResult, Status

_WORST = {Status.OK: 0, Status.WARN: 1, Status.FAIL: 2}


def run_all(cfg: Config) -> list[CheckResult]:
    return [
        checks.check_git(),
        checks.check_claude_code(),
        checks.check_gh(),
        checks.check_ollama(cfg.ollama_url, required=cfg.review.models),
        checks.check_haiku(),
        checks.check_memory_cloud(cfg.memory_cloud_url),
    ]


def overall_status(results: list[CheckResult]) -> Status:
    return max(results, key=lambda r: _WORST[r.status]).status
