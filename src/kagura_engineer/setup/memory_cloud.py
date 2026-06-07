"""Stub for memory-cloud setup. Real implementation lands in Task 9.

The setup step is intentionally a thin wrapper over doctor
`check_memory_cloud` (reachability probe). Full authed recall
smoke is Plan 3; this step only ensures the URL responds.
"""
from __future__ import annotations

from .result import StepResult, StepStatus


def ensure_memory_cloud_reachable(base_url: str, *, no_input: bool, dry_run: bool) -> StepResult:
    return StepResult(
        "memory-cloud",
        StepStatus.SKIPPED,
        "memory-cloud step not yet implemented (Task 9)",
    )
