"""`ensure_claude_login` step: install + bootstrap a Claude Code
auth source.

The auth question is shared with doctor via
`kagura_engineer.setup.auth.resolve_anthropic_auth` — that helper
returns one of {ENV_API_KEY, SUBSCRIPTION_CACHE, NONE}. The doctor
check (check_haiku) and the setup step both consume that resolution
verbatim; if the auth resolver moves, both move with it.

This step has two halves:

  - Half A — `claude` binary install (auto, no user input needed)
    curl-pipe-bash from https://claude.ai/install.sh. This is the
    same installer the official docs recommend, and it is
    idempotent: a re-run detects an existing install and no-ops.

  - Half B — credential cache bootstrap (interactive, needs_user)
    A fresh install leaves `~/.claude/.credentials.json` empty.
    The user must run `claude` once interactively (or set
    `ANTHROPIC_API_KEY`) for the cache to populate. This half
    cannot be auto-driven — there is no way to do an OAuth dance
    from inside a non-interactive `setup` run. The fix_hint is
    a copy-paste-able one-liner.

Half A and Half B are independent failures:
  - If only Half A succeeds (no auth) -> NEEDS_USER.
  - If Half A fails (no curl / network / 404) -> FAIL.
  - If Half A succeeds AND auth present -> OK.
  - If --no-input is set, Half B's NEEDS_USER becomes FAIL loud.
"""
from __future__ import annotations

import shutil
import subprocess
import time

from .auth import AuthMethod, resolve_anthropic_auth
from .result import StepResult, StepStatus

INSTALL_URL = "https://claude.ai/install.sh"
_INSTALL_TIMEOUT_S = 120


def _curl_available() -> bool:
    return shutil.which("curl") is not None


def _install_claude() -> StepResult | None:
    """Run the official installer; return a FAIL StepResult on
    failure, or None on success. Callers layer their own auth
    check on top of a successful install."""
    if not _curl_available():
        return StepResult(
            "claude-code",
            StepStatus.NEEDS_USER,
            "claude not on PATH and curl not available for auto-install",
            fix_hint=(
                "install Claude Code from https://claude.ai/download "
                "(the official install.sh requires curl)"
            ),
        )
    # `-fsSL` mirrors the official install instructions:
    #   -f fail on HTTP error (so a 404 surfaces as a non-zero exit)
    #   -s silent (no progress meter, easier to read in CI logs)
    #   -S still show errors
    #   -L follow redirects (the URL has moved at least once)
    cmd = ["curl", "-fsSL", INSTALL_URL, "|", "bash"]
    # We cannot run a pipeline directly via subprocess.run with a
    # list; we instead pass it through `sh -c` so the pipe is honored
    # on POSIX. On Windows v1 (no auto-install) we never reach here.
    try:
        proc = subprocess.run(
            " ".join(cmd),
            shell=True,
            capture_output=True,
            text=True,
            timeout=_INSTALL_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired) as exc:
        return StepResult(
            "claude-code",
            StepStatus.FAIL,
            f"install command failed: {type(exc).__name__}: {exc}",
            fix_hint=f"try running the install command manually: curl -fsSL {INSTALL_URL} | bash",
        )
    if proc.returncode != 0:
        stderr_tail = (proc.stderr or "").strip().splitlines()[-1] if proc.stderr else ""
        return StepResult(
            "claude-code",
            StepStatus.FAIL,
            f"install exited {proc.returncode}: {stderr_tail or '(no stderr)'}",
            fix_hint=f"run the install manually: curl -fsSL {INSTALL_URL} | bash",
        )
    return None


def ensure_claude_login(*, no_input: bool, dry_run: bool) -> StepResult:
    name = "claude-code"
    started = time.monotonic()

    # If claude is already on PATH, we still need to verify auth —
    # the binary being present does not mean a login has happened.
    if shutil.which("claude") is None:
        if dry_run:
            return StepResult(
                name,
                StepStatus.OK,
                f"dry-run: would run `curl -fsSL {INSTALL_URL} | bash` then prompt for login",
                duration_s=time.monotonic() - started,
            )
        install_result = _install_claude()
        if install_result is not None:
            return install_result
        # Install succeeded; re-check PATH.
        if shutil.which("claude") is None:
            return StepResult(
                name,
                StepStatus.FAIL,
                "install command exited 0 but `claude` is still not on PATH",
                fix_hint="open a new shell so PATH is refreshed, then re-run setup",
                duration_s=time.monotonic() - started,
            )

    # Auth check (shared with doctor).
    res = resolve_anthropic_auth()
    if res.method is AuthMethod.NONE:
        if no_input:
            return StepResult(
                name,
                StepStatus.FAIL,
                "no auth source and --no-input refuses to prompt",
                fix_hint=(
                    "either export ANTHROPIC_API_KEY=... or drop --no-input "
                    "and run `claude` once interactively to bootstrap a subscription login"
                ),
                duration_s=time.monotonic() - started,
            )
        return StepResult(
            name,
            StepStatus.NEEDS_USER,
            "claude installed but no auth source (env var or credential cache)",
            fix_hint=(
                "run `claude` once interactively to establish a subscription login, "
                "or export ANTHROPIC_API_KEY=... before re-running setup"
            ),
            duration_s=time.monotonic() - started,
        )

    # Auth present.
    if res.method is AuthMethod.ENV_API_KEY:
        detail = "ANTHROPIC_API_KEY set"
    else:
        detail = res.detail  # subscription cache detail (with kind label)
    return StepResult(name, StepStatus.OK, detail, duration_s=time.monotonic() - started)
