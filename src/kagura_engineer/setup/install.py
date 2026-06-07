"""Shared install helper used by `git`, `gh`, and any future step
that runs a package-manager command.

`run_install` is the one place that decides:

  - how to time the install (started = time.monotonic())
  - how to translate exceptions to `StepStatus.FAIL`
  - how to post-verify the binary is on PATH after install
  - the dry-run preview shape
  - the `--no-input` escalation from NEEDS_USER to FAIL

It is NOT used by the auth-only and verify-only halves of the
existing steps (claude.py auth, gh auth status): those checks do
not fit the 'run a package-manager command and verify the binary'
shape. Each per-step module keeps its own `install_command(platform)`
table and pre-install check (`shutil.which`), and delegates only
the 'run the install command and report a StepResult' boilerplate
here.

Why a helper, not a base class
------------------------------

The duplication is real (5 steps × ~50 lines of post-install
boilerplate each), but inheritance would force every step to
extend a `BinaryInstallStep` base even when half its logic is
auth probing. Composition is cheaper: a free function that takes
the caller's command list and the binary name to verify, and
returns a StepResult. The per-step file stays flat and
readable; the helper is the single place to change 'timeout is
now 60s, not 120s' or 'verify by reading the binary version, not
by `which`'.
"""
from __future__ import annotations

import shutil
import subprocess
import time

from .platform import OSKind, PlatformInfo
from .result import StepResult, StepStatus

# Per-platform hint copy. The `pkg_manager` variant is the common
# case (apt/dnf/pacman/brew); the windows variant names winget AND
# git-scm.com because some Windows environments don't have App
# Installer.
NEEDS_USER_HINT_PKG_MANAGER = (
    "install via your package manager (brew/apt/dnf/pacman) and re-run setup"
)
NEEDS_USER_HINT_WINDOWS = (
    "install via `winget install --id Git.Git -e --source winget` or download from "
    "https://git-scm.com/download/win"
)

_INSTALL_TIMEOUT_S = 120
_NO_INPUT_NO_CMD_HINT = (
    "drop --no-input to allow guided install, or install the tool manually and re-run setup"
)


def _needs_user_hint(platform: PlatformInfo) -> str:
    if platform.os is OSKind.WINDOWS:
        return NEEDS_USER_HINT_WINDOWS
    return NEEDS_USER_HINT_PKG_MANAGER


def run_install(
    *,
    step_name: str,
    binary: str | None,
    cmd: list[str] | None,
    platform: PlatformInfo,
    dry_run: bool,
    no_input: bool,
    timeout_s: int = _INSTALL_TIMEOUT_S,
) -> StepResult:
    """Run a package-manager install command and translate the
    outcome into a StepResult.

    The contract is: a successful install means subprocess exits 0
    AND (if `binary` is provided) the named binary is on PATH
    afterwards. A pre-install PATH check is the caller's job
    (typically `shutil.which(binary)` before calling `run_install`).

    `binary=None` opts out of the post-install verification; the
    caller's "is it installed?" check is a different probe (e.g. an
    HTTP GET to the daemon for `ollama`).

    `cmd=None` means the caller has no install strategy for this
    platform — return NEEDS_USER (or FAIL with --no-input).
    """
    started = time.monotonic()

    if cmd is None:
        if no_input:
            return StepResult(
                step_name,
                StepStatus.FAIL,
                "no install strategy; --no-input refuses to prompt",
                fix_hint=_NO_INPUT_NO_CMD_HINT,
                duration_s=time.monotonic() - started,
            )
        return StepResult(
            step_name,
            StepStatus.NEEDS_USER,
            "no install strategy for this platform",
            fix_hint=_needs_user_hint(platform),
            duration_s=time.monotonic() - started,
        )

    if dry_run:
        return StepResult(
            step_name,
            StepStatus.OK,
            f"dry-run: would run `{' '.join(cmd)}`",
            duration_s=time.monotonic() - started,
        )

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s
        )
    except subprocess.TimeoutExpired as exc:
        return StepResult(
            step_name,
            StepStatus.FAIL,
            f"install timed out after {timeout_s}s",
            fix_hint=f"run `{' '.join(cmd)}` manually to see the error",
            duration_s=time.monotonic() - started,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return StepResult(
            step_name,
            StepStatus.FAIL,
            f"install command failed to start: {type(exc).__name__}: {exc}",
            fix_hint=f"run `{' '.join(cmd)}` manually to see the error",
            duration_s=time.monotonic() - started,
        )

    if proc.returncode != 0:
        stderr_tail = (proc.stderr or "").strip().splitlines()[-1] if proc.stderr else ""
        return StepResult(
            step_name,
            StepStatus.FAIL,
            f"install exited {proc.returncode}: {stderr_tail or '(no stderr)'}",
            fix_hint=f"run `{' '.join(cmd)}` manually to see the error",
            duration_s=time.monotonic() - started,
        )

    if binary is not None and shutil.which(binary) is None:
        return StepResult(
            step_name,
            StepStatus.FAIL,
            "install command exited 0 but the binary is still not on PATH",
            fix_hint="open a new shell so PATH is refreshed, then re-run setup",
            duration_s=time.monotonic() - started,
        )

    return StepResult(
        step_name,
        StepStatus.OK,
        f"installed via `{' '.join(cmd)}`",
        duration_s=time.monotonic() - started,
    )
