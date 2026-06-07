"""Stub for ollama setup. Real implementation lands in Task 8."""
from __future__ import annotations

from .platform import PlatformInfo
from .result import StepResult, StepStatus


def ensure_ollama_up(
    platform: PlatformInfo, ollama_url: str, *, no_input: bool, dry_run: bool
) -> StepResult:
    return StepResult("ollama", StepStatus.SKIPPED, "ollama step not yet implemented (Task 8)")


def pull_ollama_models(
    platform: PlatformInfo, ollama_url: str, required: list[str], *, no_input: bool, dry_run: bool
) -> StepResult:
    return StepResult("ollama-models", StepStatus.SKIPPED, "ollama models step not yet implemented (Task 8)")
