from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from ..setup.auth import AuthMethod, resolve_anthropic_auth
from ..setup.memory_auth import MemoryAuthMethod, resolve_memory_cloud_auth
from ..setup.ollama import model_present
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
            "install git via your package manager (brew/apt/dnf/pacman) and re-run doctor",
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
            "install Claude Code (https://claude.ai/download) and re-run doctor",
        )
    try:
        proc = _run(["claude", "--version"])
    except (OSError, subprocess.SubprocessError) as exc:
        return CheckResult(
            "claude-code", Status.FAIL, f"claude invocation failed: {exc}", None
        )
    if proc.returncode != 0:
        return CheckResult(
            "claude-code",
            Status.FAIL,
            f"`claude --version` exited {proc.returncode}",
            "reinstall/repair Claude Code",
        )
    version = proc.stdout.strip() or "unknown"
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key is not None and key == "":
        return CheckResult(
            "claude-code",
            Status.FAIL,
            f"v{version}, ANTHROPIC_API_KEY is set to empty string",
            "unset it to fall back to your `claude login` subscription "
            "(recommended), or set a real value (`export ANTHROPIC_API_KEY=sk-ant-...`)",
        )
    if key:
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
            "gh", Status.FAIL, "gh not found on PATH", "install gh (https://cli.github.com/) and re-run doctor"
        )
    try:
        proc = _run(["gh", "auth", "status"])
    except (OSError, subprocess.SubprocessError) as exc:
        return CheckResult("gh", Status.FAIL, f"gh invocation failed: {exc}", None)
    if proc.returncode == 0:
        return CheckResult("gh", Status.OK, "authenticated")
    return CheckResult("gh", Status.FAIL, "not authenticated", "gh auth login")


def _http_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=_TIMEOUT) as resp:  # noqa: S310 (trusted config URL)
        return json.loads(resp.read())


def _http_reach(url: str) -> None:
    """Open url to confirm reachability; raises on connection/HTTP error. Body ignored."""
    with urllib.request.urlopen(url, timeout=_TIMEOUT) as resp:  # noqa: S310 (trusted config URL)
        resp.read()  # body discarded; open succeeding is sufficient proof of reachability


def check_ollama(base_url: str, required: list[str]) -> CheckResult:
    try:
        data = _http_json(f"{base_url.rstrip('/')}/api/tags")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return CheckResult(
            "ollama", Status.FAIL, f"daemon unreachable: {exc}", "ollama serve"
        )
    if not isinstance(data, dict):
        return CheckResult(
            "ollama",
            Status.WARN,
            "unexpected /api/tags response shape",
            "verify the ollama_url points at an Ollama daemon",
        )
    # Skip entries that aren't dicts or lack a (truthy) `name`; otherwise a
    # malformed entry injects None into `have`, inflating the count and
    # crashing the base-name match via None.split().
    have = {
        name
        for m in (data.get("models") or [])
        if isinstance(m, dict) and (name := m.get("name"))
    }
    missing = [m for m in required if not model_present(m, have)]
    if missing:
        return CheckResult(
            "ollama",
            Status.WARN,
            f"missing models: {', '.join(missing)}",
            f"ollama pull {' && ollama pull '.join(missing)}",
        )
    return CheckResult("ollama", Status.OK, f"{len(have)} models available")


def check_haiku() -> CheckResult:
    # Empty string is a deliberate "unset" signal; surface it as a
    # config error so the user fixes it before re-running.
    raw = os.environ.get("ANTHROPIC_API_KEY")
    if raw is not None and raw == "":
        return CheckResult(
            "haiku",
            Status.FAIL,
            "ANTHROPIC_API_KEY is set to empty string",
            "unset or set a real value",
        )
    res = resolve_anthropic_auth()
    if res.method is AuthMethod.ENV_API_KEY:
        return CheckResult(
            "haiku",
            Status.OK,
            "env ANTHROPIC_API_KEY is set; no API probe in P1",
        )
    if res.method is AuthMethod.SUBSCRIPTION_CACHE:
        # Decorate the detail with cache age (informational, not gating).
        assert res.cache_path is not None
        try:
            age_days = (time.time() - res.cache_path.stat().st_mtime) / 86400
        except OSError:
            age_days = 0
        return CheckResult(
            "haiku",
            Status.OK,
            f"{res.detail}, {age_days:.0f}d old; no live probe in P1",
        )
    return CheckResult(
        "haiku",
        Status.WARN,
        "no API key; relies on Claude Code subscription path (P1: env presence only, no live probe)",
        "set ANTHROPIC_API_KEY or run `claude` once interactively to establish a subscription login",
    )


# The canonical fix for a missing Memory Cloud credential. Names BOTH
# supported sources (issue #6 acceptance: env key and `kagura auth login`
# are both honoured, env-first) so the hint matches what `run/memory.py`
# actually consumes — no more README/code mismatch.
_MEMORY_AUTH_HINT = (
    "export KAGURA_API_KEY=... or run `kagura auth login` to authenticate Memory Cloud"
)


def check_memory_cloud(
    base_url: str,
    *,
    env: dict[str, str] | None = None,
    home: "Path | None" = None,
) -> CheckResult:
    # Resolve the credential FIRST (cheap, local). Unlike the old probe this
    # check no longer passes silently when the host is up but no credential
    # resolves — that is the exact first-run footgun from issue #6 (doctor
    # passes, `run` dies). env/home are injectable for tests; production reads
    # os.environ / Path.home() via the resolver's defaults.
    auth = resolve_memory_cloud_auth(env=env, home=home)

    # Extract host-only form so that any userinfo (basic auth) embedded in
    # `memory_cloud_url` is NOT echoed into the doctor detail string —
    # `doctor --json` is a common artefact in CI logs and chat pastes.
    # `urlparse(...).hostname` drops username:password@ automatically.
    from urllib.parse import urlparse
    try:
        host_only = urlparse(base_url).hostname or base_url
    except (ValueError, TypeError):
        host_only = base_url
    try:
        _http_reach(f"{base_url.rstrip('/')}/health")
    except urllib.error.HTTPError as exc:
        # An HTTP response (even 4xx/5xx) proves the host is reachable. A 4xx is
        # often the auth layer itself rejecting an absent/bad credential, so when
        # no credential resolves we point straight at the fix instead of deferring.
        if auth.method is MemoryAuthMethod.NONE:
            return CheckResult(
                "memory-cloud",
                Status.WARN,
                f"reachable but /health returned HTTP {exc.code}; no credential resolves",
                _MEMORY_AUTH_HINT,
            )
        return CheckResult(
            "memory-cloud",
            Status.WARN,
            f"reachable but /health returned HTTP {exc.code} (auth={auth.detail})",
            "verify the Memory Cloud endpoint / credential",
        )
    except (urllib.error.URLError, OSError, ValueError) as exc:
        # ValueError covers a malformed/schemeless memory_cloud_url
        # ("unknown url type" / "Invalid IPv6 URL"); urlopen raises it
        # before any network attempt. Match check_ollama, which already
        # guards ValueError, so a bad URL FAILs cleanly instead of
        # crashing the whole doctor command (run_all has no isolation).
        return CheckResult(
            "memory-cloud",
            Status.FAIL,
            f"unreachable: {exc}",
            "check config.memory_cloud_url / network",
        )
    # Host is up. Now gate on the credential: a reachable host with no
    # resolvable credential is a WARN, not an OK.
    if auth.method is MemoryAuthMethod.NONE:
        return CheckResult(
            "memory-cloud",
            Status.WARN,
            f"reachable at {host_only}, but no Memory Cloud credential resolves",
            _MEMORY_AUTH_HINT,
        )
    return CheckResult(
        "memory-cloud", Status.OK, f"reachable at {host_only}; auth={auth.detail}"
    )


def check_local_memory(path: str) -> CheckResult:
    """Verify the offline SQLite memory backend (Plan 5) can be created and
    written at `path` — the local counterpart to the cloud reachability probe."""
    db = Path(path)
    try:
        db.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return CheckResult(
            "memory-local",
            Status.FAIL,
            f"cannot create directory {db.parent}: {exc}",
            "set config.local_memory_path to a writable location",
        )
    try:
        conn = sqlite3.connect(str(db))
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS _doctor_probe (x INTEGER)")
            conn.execute("DROP TABLE _doctor_probe")
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return CheckResult(
            "memory-local",
            Status.FAIL,
            f"cannot open/write SQLite db at {db}: {exc}",
            "set config.local_memory_path to a writable location",
        )
    return CheckResult("memory-local", Status.OK, f"writable at {db}")


def check_gh_issue_driven() -> CheckResult:
    """Verify the gh-issue-driven plugin is installed.

    `run` (Plan 3) drives gh-issue-driven via headless claude; without the
    plugin the run would die deep inside a session. This is a blocking
    check (Status.FAIL ⇒ CheckResult.is_blocking), so `run`'s guard can
    refuse to start. Plugin root is overridable via KAGURA_PLUGINS_DIR
    for tests.
    """
    root = Path(os.environ.get("KAGURA_PLUGINS_DIR") or str(Path.home() / ".claude" / "plugins"))
    hits = [p for p in root.glob("**/gh-issue-driven") if p.is_dir()] if root.exists() else []
    if hits:
        return CheckResult("gh-issue-driven", Status.OK, "plugin installed")
    return CheckResult(
        "gh-issue-driven",
        Status.FAIL,
        "gh-issue-driven plugin not found",
        "install the gh-issue-driven Claude Code plugin (run requires it)",
    )
