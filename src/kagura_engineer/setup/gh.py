"""`ensure_gh_auth` step: install `gh` CLI and confirm a GitHub auth
source.

Auth question has three legitimate answers, in priority order:

  1. Token passthrough (GITHUB_TOKEN or GH_TOKEN env var) — `gh`
     accepts these without an interactive login. Useful in CI and
     in containers where running `gh auth login` is impractical.
  2. OAuth login — `gh auth status` exits 0 because the user has
     run `gh auth login` at some point and stored credentials in
     `~/.config/gh/hosts.yml` (or its Windows equivalent).
  3. None — surface NEEDS_USER with the right hint, which depends
     on whether the user is on an interactive workstation (browser
     flow) or in CI (set the env var).

This is the same priority as the Q2 spec call in plan-2-setup.md
§3.2: env wins, daemon (here, `gh auth status`) is the fallback.
The token branch is checked BEFORE invoking `gh` so that CI does
not have to authenticate the daemon, only present the token.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time

from .._launch import run_text
from .install import stderr_tail
from .platform import OSKind, PkgManagerKind, PlatformInfo
from .result import StepResult, StepStatus

_INSTALL_TIMEOUT_S = 120
_STATUS_TIMEOUT_S = 5


def install_command(platform: PlatformInfo) -> list[str] | None:
    pkg, has_sudo = platform.pkg_manager, platform.has_sudo
    if platform.os is OSKind.DARWIN and pkg is PkgManagerKind.BREW:
        return ["brew", "install", "gh"]
    if platform.os is OSKind.LINUX and pkg is PkgManagerKind.APT and has_sudo:
        return ["sudo", "apt-get", "install", "-y", "gh"]
    if platform.os is OSKind.LINUX and pkg is PkgManagerKind.DNF and has_sudo:
        return ["sudo", "dnf", "install", "-y", "gh"]
    if platform.os is OSKind.LINUX and pkg is PkgManagerKind.PACMAN and has_sudo:
        return ["sudo", "pacman", "-S", "--noconfirm", "gh"]
    if platform.os is OSKind.WINDOWS and pkg is PkgManagerKind.WINGET:
        return ["winget", "install", "--id", "GitHub.cli", "-e", "--source", "winget"]
    return None


def _has_token_env() -> str | None:
    """Return the name of the first non-empty token env var, or None."""
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        raw = os.environ.get(var)
        if raw:  # empty string falls through (matches doctor behavior)
            return var
    return None


def _run_gh_status() -> subprocess.CompletedProcess:
    # `gh auth status` exits 0 when authenticated, 1 (sometimes 4) when
    # not. Output is on stdout; we don't currently parse it, but we
    # capture for the detail string.
    return run_text(
        ["gh", "auth", "status"],
        capture_output=True,
        timeout=_STATUS_TIMEOUT_S,
    )


def ensure_gh_auth(
    platform: PlatformInfo, *, no_input: bool, dry_run: bool
) -> StepResult:
    name = "gh"
    started = time.monotonic()

    # 1. Already on PATH?
    if shutil.which("gh") is not None:
        # 1a. Token passthrough first (no daemon invocation).
        env_var = _has_token_env()
        if env_var is not None:
            return StepResult(
                name,
                StepStatus.OK,
                f"using {env_var} env (token passthrough)",
                duration_s=time.monotonic() - started,
            )
        # 1b. Dry-run: a preview executes nothing. We can't confirm auth
        # without running `gh auth status`, so report it as a step that
        # would be verified — and never hard-FAIL under --no-input here.
        if dry_run:
            return StepResult(
                name,
                StepStatus.NEEDS_USER,
                "dry-run: gh on PATH; would verify `gh auth status` (and prompt login if unauthenticated)",
                fix_hint=(
                    "run setup without --dry-run to verify; set GITHUB_TOKEN=ghp_... "
                    "for token passthrough"
                ),
                duration_s=time.monotonic() - started,
            )
        # 1c. Live auth status check.
        try:
            proc = _run_gh_status()
        except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired) as exc:
            return StepResult(
                name,
                StepStatus.FAIL,
                f"`gh auth status` failed: {type(exc).__name__}: {exc}",
                fix_hint="re-run setup; if this persists, file a doctor bug",
                duration_s=time.monotonic() - started,
            )
        if proc.returncode == 0:
            return StepResult(
                name,
                StepStatus.OK,
                f"gh auth status OK",
                duration_s=time.monotonic() - started,
            )
        # Not authed. We respect --no-input.
        if no_input:
            return StepResult(
                name,
                StepStatus.FAIL,
                "gh is on PATH but not authed; --no-input refuses to prompt",
                fix_hint=(
                    "export GITHUB_TOKEN=ghp_... (or run `gh auth login` "
                    "interactively) and re-run setup"
                ),
                duration_s=time.monotonic() - started,
            )
        return StepResult(
            name,
            StepStatus.NEEDS_USER,
            "gh is on PATH but not authed",
            fix_hint=(
                "run `gh auth login` (browser flow), or set "
                "GITHUB_TOKEN=ghp_... for token passthrough"
            ),
            duration_s=time.monotonic() - started,
        )

    # 2. Not installed. Dry-run preview.
    if dry_run:
        cmd = install_command(platform)
        if cmd is None:
            return StepResult(
                name,
                StepStatus.NEEDS_USER,
                "dry-run: gh missing; no install strategy for this platform",
                fix_hint="install gh from https://cli.github.com/ then re-run setup",
                duration_s=time.monotonic() - started,
            )
        return StepResult(
            name,
            StepStatus.OK,
            f"dry-run: would run `{' '.join(cmd)}` and require `gh auth login`",
            duration_s=time.monotonic() - started,
        )

    # 3. Not installed, real run. Build install command.
    cmd = install_command(platform)
    if cmd is None:
        if no_input:
            return StepResult(
                name,
                StepStatus.FAIL,
                "gh missing and no install strategy; --no-input refuses to prompt",
                fix_hint=(
                    "install gh from https://cli.github.com/ and re-run setup "
                    "(drop --no-input to allow guided install)"
                ),
                duration_s=time.monotonic() - started,
            )
        return StepResult(
            name,
            StepStatus.NEEDS_USER,
            "gh missing; auto-install unavailable on this platform",
            fix_hint="install gh from https://cli.github.com/ and re-run setup",
            duration_s=time.monotonic() - started,
        )

    # 4. Auto-install.
    try:
        proc = run_text(
            cmd, capture_output=True, timeout=_INSTALL_TIMEOUT_S
        )
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired) as exc:
        return StepResult(
            name,
            StepStatus.FAIL,
            f"install command failed: {type(exc).__name__}: {exc}",
            fix_hint=f"run `{' '.join(cmd)}` manually to see the error",
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
    if shutil.which("gh") is None:
        return StepResult(
            name,
            StepStatus.FAIL,
            "install command exited 0 but `gh` is still not on PATH",
            fix_hint="open a new shell so PATH is refreshed, then re-run setup",
            duration_s=time.monotonic() - started,
        )

    # 5. Just-installed. Now we need auth. Surface NEEDS_USER unless
    # the token env is already set, in which case we are good.
    env_var = _has_token_env()
    if env_var is not None:
        return StepResult(
            name,
            StepStatus.OK,
            f"installed via `{' '.join(cmd)}`; using {env_var} env (token passthrough)",
            duration_s=time.monotonic() - started,
        )
    if no_input:
        return StepResult(
            name,
            StepStatus.FAIL,
            "gh installed; auth required; --no-input refuses to prompt",
            fix_hint=(
                "export GITHUB_TOKEN=ghp_... (or run `gh auth login` "
                "interactively) and re-run setup"
            ),
            duration_s=time.monotonic() - started,
        )
    return StepResult(
        name,
        StepStatus.NEEDS_USER,
        "gh installed; auth required",
        fix_hint=(
            "run `gh auth login` (browser flow), or set "
            "GITHUB_TOKEN=ghp_... for token passthrough"
        ),
        duration_s=time.monotonic() - started,
    )
