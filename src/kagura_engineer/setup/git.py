"""`ensure_git` step: idempotent install of the `git` CLI.

Pattern that all install steps (claude, gh, ollama) will follow:

  1. Check if the binary is already on PATH. If yes -> OK, done.
  2. If the user passed `--dry-run`, stop here and report what we
     *would* have done.
  3. If the platform cannot auto-install (no sudo, Windows v1,
     unknown OS) -> NEEDS_USER with a hand-install hint, unless
     `--no-input` is set, in which case we FAIL loudly so the CI
     job surfaces a non-zero exit code.
  4. Otherwise, run the install command and report success/failure.

This file is intentionally small — the same shape repeats 4 more
times. If we end up with 5 nearly-identical `ensure_X` functions, the
right refactor is to extract a `_run_install(step_name, build_command,
platform, *, no_input, dry_run)` helper in `__init__.py`. We do that
refactor in Task 6 (orchestrator) where the duplication is no longer
just imagined.
"""
from __future__ import annotations

import shutil
import subprocess
import time

from .install import stderr_tail
from .platform import OSKind, PkgManagerKind, PlatformInfo
from .result import StepResult, StepStatus

_INSTALL_TIMEOUT_S = 120  # network/disk bound; 2 minutes is plenty


def install_command(platform: PlatformInfo) -> list[str] | None:
    """Build the install command for `git` on this platform, or None
    if we don't know how to install it (caller escalates to
    NEEDS_USER).

    The `has_sudo` flag is read from the platform profile: on Linux
    with apt/dnf/pacman we always prefix `sudo` (the alternative is
    failing in 99% of cases), on macOS we never do (`brew install`
    must run as the user, not root, or it chokes on permissions).
    """
    pkg, has_sudo = platform.pkg_manager, platform.has_sudo
    if platform.os is OSKind.DARWIN and pkg is PkgManagerKind.BREW:
        return ["brew", "install", "git"]
    if platform.os is OSKind.LINUX and pkg is PkgManagerKind.APT and has_sudo:
        return ["sudo", "apt-get", "install", "-y", "git"]
    if platform.os is OSKind.LINUX and pkg is PkgManagerKind.DNF and has_sudo:
        return ["sudo", "dnf", "install", "-y", "git"]
    if platform.os is OSKind.LINUX and pkg is PkgManagerKind.PACMAN and has_sudo:
        # pacman uses --noconfirm, not -y.
        return ["sudo", "pacman", "-S", "--noconfirm", "git"]
    # Windows: Plan 2 v1 only hints at Windows; we do not auto-install.
    # Returning None here forces the caller to surface a NEEDS_USER
    # fix_hint with the winget/git-scm.com URL.
    return None


def ensure_git(
    platform: PlatformInfo, *, no_input: bool, dry_run: bool
) -> StepResult:
    name = "git"
    started = time.monotonic()

    # 1. Already on PATH?
    if shutil.which("git") is not None:
        return StepResult(
            name,
            StepStatus.OK,
            "git already on PATH",
            duration_s=time.monotonic() - started,
        )

    # 2. Dry-run: preview only.
    if dry_run:
        cmd = install_command(platform)
        if cmd is None:
            return StepResult(
                name,
                StepStatus.NEEDS_USER,
                "dry-run: git missing; no install strategy for this platform",
                fix_hint="install git manually, then re-run setup",
                duration_s=time.monotonic() - started,
            )
        return StepResult(
            name,
            StepStatus.OK,
            f"dry-run: would run `{' '.join(cmd)}`",
            duration_s=time.monotonic() - started,
        )

    # 3. Need install. Build command; if we cannot, surface NEEDS_USER.
    cmd = install_command(platform)
    if cmd is None:
        if no_input:
            return StepResult(
                name,
                StepStatus.FAIL,
                "git missing and no install strategy; --no-input refuses to prompt",
                fix_hint=(
                    "install git manually for this platform, then re-run setup"
                    " (drop --no-input to allow interactive install)"
                ),
                duration_s=time.monotonic() - started,
            )
        if platform.os is OSKind.WINDOWS:
            hint = "install git via `winget install --id Git.Git -e --source winget` or download from https://git-scm.com/download/win"
        else:
            hint = "install git via your package manager (brew/apt/dnf/pacman) and re-run setup"
        return StepResult(
            name,
            StepStatus.NEEDS_USER,
            "git missing; auto-install unavailable on this platform",
            fix_hint=hint,
            duration_s=time.monotonic() - started,
        )

    # 4. Auto-install path.
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_INSTALL_TIMEOUT_S
        )
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired) as exc:
        return StepResult(
            name,
            StepStatus.FAIL,
            f"install command failed to start: {type(exc).__name__}: {exc}",
            fix_hint=f"try running `{' '.join(cmd)}` manually to see the error",
            duration_s=time.monotonic() - started,
        )

    if proc.returncode != 0:
        return StepResult(
            name,
            StepStatus.FAIL,
            f"install exited {proc.returncode}: {stderr_tail(proc.stderr) or '(no stderr)'}",
            fix_hint=f"run `{' '.join(cmd)}` manually to see the error",
            duration_s=time.monotonic() - started,
        )

    # Verify the binary is now reachable. A successful exit code does
    # not guarantee that PATH has been refreshed; some package managers
    # install to /usr/local/bin which may not be on PATH for the next
    # subprocess.
    if shutil.which("git") is None:
        return StepResult(
            name,
            StepStatus.FAIL,
            "install command exited 0 but `git` is still not on PATH",
            fix_hint="open a new shell so PATH is refreshed, then re-run setup",
            duration_s=time.monotonic() - started,
        )

    return StepResult(
        name,
        StepStatus.OK,
        f"installed via `{' '.join(cmd)}`",
        duration_s=time.monotonic() - started,
    )
