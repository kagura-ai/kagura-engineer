"""`ensure_ollama_up` + `pull_ollama_models` steps.

These are the two ollama-flavored steps in the setup plan. They
share a probe (the /api/tags endpoint of the local daemon) and
a binary (the `ollama` CLI on PATH); the split is purely the
action they take.

`ensure_ollama_up` answers: "is the daemon running, and if not,
can we start it?" The auto-start attempt is `ollama serve &`
with a short wait; if the daemon still does not answer, we
surface NEEDS_USER with a hand-run hint. We do NOT block on
Popen for an unbounded time — a hung `ollama serve` would
otherwise stall the whole `setup` run.

`pull_ollama_models` answers: "are the configured models
present locally, and if not, can we pull them?" Pulls are
non-interactive (`ollama pull <name>`), so `--no-input` does
not block this step — it just runs to completion. The action
is naturally idempotent: `ollama pull` is a no-op when the
model is already present.

`ollama` not installed is the same shape as `gh` not installed:
the install_command table gives us the package-manager
command; run_install does the heavy lifting. See install.py
for the post-verify and exception-translation contract.

Probe contract:
  GET {ollama_url}/api/tags  -> 200 with {"models": [...]}
  Anything else (HTTPError, URLError, OSError) is treated as
  'daemon is not up'. The body shape is shared with doctor
  check_ollama; the symmetric model matching (`qwen2.5-coder:7b`
  matches `qwen2.5-coder`) lives there, not here — we only need
  the set of names, not the comparison, for the pull step.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request

from .install import run_install, stderr_tail
from .platform import OSKind, PkgManagerKind, PlatformInfo
from .result import StepResult, StepStatus

_PROBE_TIMEOUT_S = 5
_SERVE_WAIT_S = 5
_PULL_TIMEOUT_S = 300  # a 7B model on slow link can take minutes


def install_command(platform: PlatformInfo) -> list[str] | None:
    pkg, has_sudo = platform.pkg_manager, platform.has_sudo
    if platform.os is OSKind.DARWIN and pkg is PkgManagerKind.BREW:
        return ["brew", "install", "ollama"]
    if platform.os is OSKind.LINUX and pkg is PkgManagerKind.APT and has_sudo:
        return ["sudo", "apt-get", "install", "-y", "ollama"]
    if platform.os is OSKind.LINUX and pkg is PkgManagerKind.DNF and has_sudo:
        return ["sudo", "dnf", "install", "-y", "ollama"]
    if platform.os is OSKind.LINUX and pkg is PkgManagerKind.PACMAN and has_sudo:
        return ["sudo", "pacman", "-S", "--noconfirm", "ollama"]
    if platform.os is OSKind.WINDOWS and pkg is PkgManagerKind.WINGET:
        return ["winget", "install", "--id", "Ollama.Ollama", "-e", "--source", "winget"]
    return None


def model_present(req: str, have: set[str]) -> bool:
    """Is required model `req` satisfied by the daemon's `have` set?

    Symmetric base-name match: a tagged config (`foo:7b`) matches an
    untagged daemon entry (`foo`) and vice versa. Both sides are
    normalized to the part before the first ':' before comparing.

    Shared with doctor.check_ollama so the two never disagree on whether
    a model is present — otherwise doctor reports OK while setup re-pulls
    the same model under a different tag.
    """
    if req in have:
        return True
    req_base = req.split(":", 1)[0]
    return any(h.split(":", 1)[0] == req_base for h in have)


def _names_from_tags(data: dict) -> set[str]:
    """Build the set of model names from a /api/tags body, skipping
    entries that are not dicts or lack a (truthy) `name` — those would
    otherwise inject None into the set and break matching/counts."""
    return {
        name
        for m in (data.get("models") or [])
        if isinstance(m, dict) and (name := m.get("name"))
    }


def _probe_daemon(ollama_url: str) -> dict:
    """Hit /api/tags; return the parsed JSON body. Caller catches
    the exceptions that mean 'daemon is not up'."""
    with urllib.request.urlopen(
        f"{ollama_url.rstrip('/')}/api/tags", timeout=_PROBE_TIMEOUT_S
    ) as resp:
        return json.loads(resp.read().decode())


def _try_start_daemon() -> subprocess.Popen | None:
    """Best-effort: spawn `ollama serve` in the background, wait a few
    seconds, and return the still-running process handle (or None if it
    could not be started / exited immediately).

    We don't block on Popen for long; an `ollama serve` that takes more
    than _SERVE_WAIT_S to bind is treated as 'still coming up' and the
    handle is returned so the caller can re-probe — and terminate it if
    the daemon never actually answers the configured URL.
    """
    try:
        proc = subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    # Naive wait: a real check would poll the /api/tags endpoint.
    # We bound the wait and let the post-check be the success signal.
    try:
        proc.wait(timeout=_SERVE_WAIT_S)
    except subprocess.TimeoutExpired:
        # serve is still running; that's the expected case (a foreground
        # serve would have returned immediately on failure). Hand the
        # handle back so the caller owns its lifecycle.
        return proc
    # serve exited within the wait window -> it failed to stay up.
    return None


def _terminate(proc: subprocess.Popen | None) -> None:
    """Stop a serve process we spawned but that isn't serving the
    configured URL, so it doesn't leak as an orphan."""
    if proc is None:
        return
    try:
        proc.terminate()
    except (OSError, subprocess.SubprocessError):
        pass


def ensure_ollama_up(
    platform: PlatformInfo,
    ollama_url: str,
    *,
    no_input: bool,
    dry_run: bool,
) -> StepResult:
    name = "ollama"
    started = time.monotonic()

    # 1. Probe first — if the daemon is up, we're done regardless of
    # whether the binary is installed.
    try:
        _probe_daemon(ollama_url)
        return StepResult(
            name, StepStatus.OK,
            f"daemon reachable at {ollama_url}",
            duration_s=time.monotonic() - started,
        )
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        pass  # not up — fall through to install/serve path

    # 2. Binary on PATH?
    if shutil.which("ollama") is None:
        # 2a. Dry-run: preview install.
        if dry_run:
            cmd = install_command(platform)
            if cmd is None:
                return StepResult(
                    name, StepStatus.NEEDS_USER,
                    "dry-run: ollama missing; no install strategy",
                    fix_hint="install ollama from https://ollama.com/download",
                    duration_s=time.monotonic() - started,
                )
            return StepResult(
                name, StepStatus.OK,
                f"dry-run: would run `{' '.join(cmd)}`",
                duration_s=time.monotonic() - started,
            )
        # 2b. Install via the shared helper.
        result = run_install(
            step_name=name,
            binary="ollama",
            cmd=install_command(platform),
            platform=platform,
            dry_run=False,
            no_input=no_input,
        )
        # run_install already returned the FAIL/NEEDS_USER shape if
        # anything went wrong. If the install succeeded, the binary
        # is now on PATH; fall through to the serve attempt.
        if result.status is not StepStatus.OK:
            return result

    # 3. Binary present, daemon still down. Try to start it.
    if dry_run:
        return StepResult(
            name, StepStatus.OK,
            f"dry-run: would attempt `ollama serve &` to bring daemon up at {ollama_url}",
            duration_s=time.monotonic() - started,
        )

    serve_proc = _try_start_daemon()
    # Re-probe.
    try:
        _probe_daemon(ollama_url)
        return StepResult(
            name, StepStatus.OK,
            f"daemon now reachable at {ollama_url}",
            duration_s=time.monotonic() - started,
        )
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        pass

    # 4. Could not bring it up. The serve we spawned (if any) is not
    # answering the configured URL, so terminate it rather than leak it.
    _terminate(serve_proc)

    # Surface NEEDS_USER.
    if no_input:
        return StepResult(
            name, StepStatus.FAIL,
            "ollama daemon not reachable and --no-input refuses to prompt",
            fix_hint=(
                "start the daemon manually (`ollama serve` in a separate "
                "terminal) and re-run setup, or drop --no-input"
            ),
            duration_s=time.monotonic() - started,
        )
    return StepResult(
        name, StepStatus.NEEDS_USER,
        f"daemon at {ollama_url} not reachable; auto-start failed",
        fix_hint=(
            "run `ollama serve` in a separate terminal, then re-run setup"
        ),
        duration_s=time.monotonic() - started,
    )


def pull_ollama_models(
    platform: PlatformInfo,
    ollama_url: str,
    required: list[str],
    *,
    no_input: bool,
    dry_run: bool,
) -> StepResult:
    name = "ollama-models"
    started = time.monotonic()

    if not required:
        return StepResult(
            name, StepStatus.SKIPPED,
            "no models configured (cfg.review.models is empty)",
            duration_s=time.monotonic() - started,
        )

    # If ollama isn't on PATH, the model pull step is meaningless.
    # We don't auto-install here — ensure_ollama_up handles that.
    if shutil.which("ollama") is None:
        return StepResult(
            name, StepStatus.SKIPPED,
            "ollama not on PATH; ensure_ollama_up is responsible for the install",
            duration_s=time.monotonic() - started,
        )

    # Probe the daemon to see what's already there.
    try:
        data = _probe_daemon(ollama_url)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        return StepResult(
            name, StepStatus.FAIL,
            f"could not list ollama models: {type(exc).__name__}: {exc}",
            fix_hint="ensure the ollama daemon is running, then re-run setup",
            duration_s=time.monotonic() - started,
        )

    have = _names_from_tags(data)
    missing = [m for m in required if not model_present(m, have)]
    if not missing:
        return StepResult(
            name, StepStatus.OK,
            f"all {len(required)} configured models present",
            duration_s=time.monotonic() - started,
        )

    if dry_run:
        return StepResult(
            name, StepStatus.OK,
            f"dry-run: would run `ollama pull {' && ollama pull '.join(missing)}`",
            duration_s=time.monotonic() - started,
        )

    # Pull each missing model sequentially. `ollama pull` is
    # non-interactive; --no-input does not block this step.
    for model in missing:
        try:
            proc = subprocess.run(
                ["ollama", "pull", model],
                capture_output=True, text=True, timeout=_PULL_TIMEOUT_S,
            )
        except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired) as exc:
            return StepResult(
                name, StepStatus.FAIL,
                f"ollama pull {model} failed: {type(exc).__name__}: {exc}",
                fix_hint="check network/disk and re-run setup",
                duration_s=time.monotonic() - started,
            )
        if proc.returncode != 0:
            return StepResult(
                name, StepStatus.FAIL,
                f"ollama pull {model} exited {proc.returncode}: {stderr_tail(proc.stderr) or '(no stderr)'}",
                fix_hint=f"run `ollama pull {model}` manually to see the error",
                duration_s=time.monotonic() - started,
            )

    return StepResult(
        name, StepStatus.OK,
        f"pulled {len(missing)} missing models",
        duration_s=time.monotonic() - started,
    )
