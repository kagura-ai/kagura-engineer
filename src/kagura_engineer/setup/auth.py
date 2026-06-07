"""Anthropic auth resolution, shared by doctor and setup.

The auth question has two legitimate answers for this project:

  1. ENV_API_KEY          — `ANTHROPIC_API_KEY` is set to a non-empty
                            value. Fast, explicit, no daemon needed.
  2. SUBSCRIPTION_CACHE   — Claude Code's OAuth subscription login has
                            happened at some point. Evidence is the
                            presence of `~/.claude/.credentials.json`
                            (modern, Claude Code v1.x) or
                            `~/.claude.json` (legacy, Claude Code v0.x).
                            The cache does NOT need to be *fresh*;
                            a one-time `claude` login lasts the whole
                            subscription lifetime.

If neither is present, the user has done neither — the resolver
returns `NONE` and the caller surfaces a `NEEDS_USER` / `WARN` with
a hint to either `export ANTHROPIC_API_KEY=...` or run `claude`
once interactively.

This module is deliberately side-effect free: it reads env and
filesystem, returns an `AuthResolution` value object. The actual
claude-side action (running `claude -p` to verify, or invoking
`claude` interactively to bootstrap the credential cache) lives in
`setup.claude`. Keeping the resolver pure means doctor can call it
during `setup --no-input` validation without a TTY dependency.
"""
from __future__ import annotations

import enum
import os
from dataclasses import dataclass
from pathlib import Path


class AuthMethod(enum.Enum):
    ENV_API_KEY = "env_api_key"
    SUBSCRIPTION_CACHE = "subscription_cache"
    NONE = "none"


@dataclass(frozen=True)
class AuthResolution:
    method: AuthMethod
    # `detail` is short, human-readable, and meant to be embedded in
    # a doctor / setup detail string. The env key value is included
    # verbatim (callers who care about redaction must redact themselves;
    # the resolver's job is to find, not to hide).
    detail: str
    # Set only when method is SUBSCRIPTION_CACHE. Used by setup to
    # surface "credentials at <path> are N days old" and by doctor to
    # pick the "modern" vs "legacy" hint copy.
    cache_path: Path | None = None


def resolve_anthropic_auth(
    *, env: dict[str, str] | None = None, home: Path | None = None
) -> AuthResolution:
    """Resolve which Anthropic auth method is in effect.

    Both `env` and `home` are injectable for testability; in
    production they default to `os.environ` and `Path.home()`.
    """
    env = env if env is not None else dict(os.environ)
    home = home if home is not None else Path.home()

    # 1. Env var. We treat empty string as "not set" because the
    # conventional failure mode is a user exporting the name with no
    # value in a .env file, then later assuming they have a key.
    raw = env.get("ANTHROPIC_API_KEY")
    if raw:
        return AuthResolution(
            method=AuthMethod.ENV_API_KEY,
            detail=raw,
        )

    # 2. Subscription credential cache. We try the modern location
    # first; only fall back to the legacy one if the modern is absent
    # (not unreadable — the user might have a permission issue worth
    # knowing about, and falling through silently would hide it).
    modern = home / ".claude" / ".credentials.json"
    legacy = home / ".claude.json"
    for path, kind in ((modern, "modern"), (legacy, "legacy")):
        try:
            if path.read_text():
                return AuthResolution(
                    method=AuthMethod.SUBSCRIPTION_CACHE,
                    detail=f"subscription login detected ({kind} cache: {path})",
                    cache_path=path,
                )
        except OSError:
            # Permission denied / file missing / etc. Skip this slot
            # and let the loop try the next one.
            continue

    return AuthResolution(method=AuthMethod.NONE, detail="")
