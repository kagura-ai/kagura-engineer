"""Memory Cloud auth resolution, shared by doctor and setup.

The sibling of `setup.auth` (`resolve_anthropic_auth`), for the Memory
Cloud credential instead of the Anthropic one. There are two legitimate
answers, mirroring the Claude path:

  1. ENV_API_KEY    — `KAGURA_API_KEY` is set to a non-empty value. This is
                      what `run/memory.py` reads today (passed straight to
                      `kagura_memory.KaguraClient(api_key=...)`).
  2. OAUTH_PROFILE  — `kagura auth login` has written a profile to
                      `~/.kagura/credentials.json`. When `KAGURA_API_KEY`
                      is unset the SDK falls back to this OAuth profile, so
                      a login alone is a working credential.

**Precedence is env-first**, matching `resolve_anthropic_auth` and the
SDK's own behaviour (`from_config` passes `api_key=os.environ.get(...)`,
and a non-empty key takes priority over the profile). If neither resolves
the user has authenticated for neither, so the resolver returns `NONE` and
the caller surfaces a WARN with a hint to `export KAGURA_API_KEY=...` or
`kagura auth login`.

Like `setup.auth`, this module is deliberately side-effect free: it reads
env and the filesystem and returns a value object, so doctor can call it
during `--no-input` validation without a TTY. The credentials file is
parsed with stdlib `json` (not the SDK loader) so the resolver stays a
pure, dependency-light function over (env, home).
"""
from __future__ import annotations

import enum
import json
import os
from dataclasses import dataclass
from pathlib import Path


class MemoryAuthMethod(enum.Enum):
    ENV_API_KEY = "env_api_key"
    OAUTH_PROFILE = "oauth_profile"
    NONE = "none"


@dataclass(frozen=True)
class MemoryAuthResolution:
    method: MemoryAuthMethod
    # Short, human-readable, safe to embed in a doctor/setup detail string.
    # Never contains the key value (the env key is detected, not echoed) —
    # unlike the Anthropic resolver, since `doctor --json` lands in CI logs.
    detail: str
    # Set only when method is OAUTH_PROFILE: the resolved profile name.
    profile: str | None = None


def _credentials_path(home: Path) -> Path:
    # Matches kagura_memory.auth.credentials.DEFAULT_CREDENTIALS_PATH.
    return home / ".kagura" / "credentials.json"


def _resolve_oauth_profile(home: Path) -> str | None:
    """Return the profile name a `kagura auth login` cache would activate, or
    None if the cache is absent, unreadable, malformed, or empty.

    Mirrors the SDK's selection (`CredentialsFile.get_profile(None)` →
    `default_profile`) without importing the SDK: read the file, pick the
    `default_profile`, and confirm it actually has a profile entry.
    """
    path = _credentials_path(home)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    profiles = data.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        return None
    # The SDK defaults to "default" when `default_profile` is absent.
    key = data.get("default_profile") or "default"
    if key in profiles:
        return key
    # default_profile points at a missing entry; fall back to any present
    # profile so a usable login is still detected.
    return next(iter(profiles))


def resolve_memory_cloud_auth(
    *, env: dict[str, str] | None = None, home: Path | None = None
) -> MemoryAuthResolution:
    """Resolve which Memory Cloud auth method is in effect.

    Both `env` and `home` are injectable for testability; in production
    they default to `os.environ` and `Path.home()`.
    """
    env = env if env is not None else dict(os.environ)
    home = home if home is not None else Path.home()

    # 1. Env var. Empty string is treated as "not set" — the conventional
    # failure mode is exporting the name with no value in a .env file.
    if env.get("KAGURA_API_KEY"):
        return MemoryAuthResolution(
            method=MemoryAuthMethod.ENV_API_KEY,
            detail="env KAGURA_API_KEY is set",
        )

    # 2. `kagura auth login` OAuth profile cache.
    profile = _resolve_oauth_profile(home)
    if profile is not None:
        return MemoryAuthResolution(
            method=MemoryAuthMethod.OAUTH_PROFILE,
            detail=f"kagura auth login profile '{profile}'",
            profile=profile,
        )

    return MemoryAuthResolution(method=MemoryAuthMethod.NONE, detail="")
