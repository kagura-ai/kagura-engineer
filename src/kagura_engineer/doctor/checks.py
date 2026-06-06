from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request

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
            "unset or set a real value (e.g. `export ANTHROPIC_API_KEY=sk-ant-...`)",
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
            "gh", Status.FAIL, "gh not found on PATH", "kagura-engineer setup --fix gh"
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


def _model_present(req: str, have: set[str]) -> bool:
    if req in have:
        return True
    # untagged config name matches any tag of the same base model
    return ":" not in req and any(h.split(":", 1)[0] == req for h in have)


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
    have = {m.get("name") for m in data.get("models", []) if isinstance(m, dict)}
    missing = [m for m in required if not _model_present(m, have)]
    if missing:
        return CheckResult(
            "ollama",
            Status.WARN,
            f"missing models: {', '.join(missing)}",
            f"ollama pull {' && ollama pull '.join(missing)}",
        )
    return CheckResult("ollama", Status.OK, f"{len(have)} models available")


def check_haiku() -> CheckResult:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key is not None and key == "":
        return CheckResult(
            "haiku",
            Status.FAIL,
            "ANTHROPIC_API_KEY is set to empty string",
            "unset or set a real value",
        )
    if key:
        return CheckResult(
            "haiku",
            Status.OK,
            "env ANTHROPIC_API_KEY is set; no API probe in P1",
        )
    return CheckResult(
        "haiku",
        Status.WARN,
        "no API key; relies on Claude Code subscription path (P1: env presence only, no live probe)",
        "set ANTHROPIC_API_KEY or confirm subscription covers haiku",
    )


def check_memory_cloud(base_url: str) -> CheckResult:
    try:
        _http_reach(f"{base_url.rstrip('/')}/health")
    except urllib.error.HTTPError as exc:
        # An HTTP response (even 4xx/5xx) proves the host is reachable; this is a
        # reachability probe, not an auth/health check. Full authed recall smoke is Plan 3.
        return CheckResult(
            "memory-cloud",
            Status.WARN,
            f"reachable but /health returned HTTP {exc.code}",
            "auth/endpoint verified later by setup / Plan 3 recall smoke",
        )
    except (urllib.error.URLError, OSError) as exc:
        return CheckResult(
            "memory-cloud",
            Status.FAIL,
            f"unreachable: {exc}",
            "check config.memory_cloud_url / network",
        )
    return CheckResult("memory-cloud", Status.OK, f"reachable at {base_url}")
