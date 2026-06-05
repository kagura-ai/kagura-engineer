from __future__ import annotations

import os
import shutil
import subprocess

from .result import CheckResult, Status

_TIMEOUT = 5


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT)


def check_git() -> CheckResult:
    if shutil.which("git") is None:
        return CheckResult(
            "git",
            Status.FAIL,
            "git not found on PATH",
            "kagura-engineer setup --fix git",
        )
    try:
        proc = _run(["git", "rev-parse", "--is-inside-work-tree"])
    except (OSError, subprocess.SubprocessError) as exc:
        return CheckResult("git", Status.FAIL, f"git invocation failed: {exc}", None)
    if proc.returncode == 0 and proc.stdout.strip() == "true":
        return CheckResult("git", Status.OK, "inside a git work tree")
    return CheckResult(
        "git",
        Status.WARN,
        "not inside a git work tree",
        "cd into the target repo before running",
    )


def check_claude_code() -> CheckResult:
    if shutil.which("claude") is None:
        return CheckResult(
            "claude-code",
            Status.FAIL,
            "claude not found on PATH",
            "kagura-engineer setup --fix claude-code",
        )
    try:
        proc = _run(["claude", "--version"])
    except (OSError, subprocess.SubprocessError) as exc:
        return CheckResult(
            "claude-code", Status.FAIL, f"claude invocation failed: {exc}", None
        )
    version = proc.stdout.strip() or "unknown"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return CheckResult("claude-code", Status.OK, f"v{version}, auth=api_key")
    return CheckResult(
        "claude-code",
        Status.WARN,
        f"v{version}, auth=subscription (unverified)",
        "run `claude` once interactively to confirm subscription login",
    )


def check_gh() -> CheckResult:
    if shutil.which("gh") is None:
        return CheckResult(
            "gh", Status.FAIL, "gh not found on PATH", "kagura-engineer setup --fix gh"
        )
    try:
        proc = _run(["gh", "auth", "status"])
    except (OSError, subprocess.SubprocessError) as exc:
        return CheckResult("gh", Status.FAIL, f"gh invocation failed: {exc}", None)
    if proc.returncode == 0:
        return CheckResult("gh", Status.OK, "authenticated")
    return CheckResult("gh", Status.FAIL, "not authenticated", "gh auth login")
