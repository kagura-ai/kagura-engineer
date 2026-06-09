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

Like `setup.auth`, this module reads env and the filesystem and returns a
value object, so doctor can call it during `--no-input` validation without
a TTY. The credentials file is parsed by the **SDK** loader
(`kagura_memory.auth.credentials.load_credentials_file`) rather than a
hand-rolled re-implementation (issue #36): the SDK owns the file format,
the default-profile selection, and the malformed-file fallbacks, so
matching it by hand only re-creates the drift footgun this module exists
to close. (The loader coerces the real `~/.kagura` perms to 0700/0600 as a
benign side effect when it reads a present file.)
"""
from __future__ import annotations

import enum
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

    Delegates to the SDK loader so our selection is the SDK's selection by
    construction: `load_credentials_file` returns an empty `CredentialsFile`
    for a missing/unreadable/malformed file, and `get_profile(None)` is
    `profiles.get(default_profile)` — which is None when `default_profile`
    names a missing entry (it never falls back to an arbitrary profile).
    Reporting a profile the SDK would NOT select re-creates the exact
    "doctor passes, run dies" footgun this module exists to close.
    """
    from kagura_memory.auth.credentials import load_credentials_file

    cf = load_credentials_file(_credentials_path(home))
    if cf.get_profile() is None:
        return None
    # The active key when get_profile(None) resolves is `default_profile`.
    return cf.default_profile


def resolve_memory_cloud_auth(
    *, env: dict[str, str] | None = None, home: Path | None = None
) -> MemoryAuthResolution:
    """Resolve which Memory Cloud auth method is in effect.

    Both `env` and `home` are injectable for testability; in production
    they default to `os.environ` and `Path.home()`.
    """
    env = env if env is not None else dict(os.environ)
    home = home if home is not None else Path.home()

    # 1. Env var. Empty / whitespace-only is treated as "not set" — the
    # conventional failure mode is exporting the name with no value (or a
    # stray space) in a .env file. The `.strip()` check mirrors the SDK's
    # `_resolve_auth` precisely (`env_key and env_key.strip()`): a
    # whitespace-only key would send `Authorization: Bearer ` and 401, so
    # the SDK falls through — reporting it as present here would re-create
    # the "doctor passes, run dies" mismatch.
    if (env.get("KAGURA_API_KEY") or "").strip():
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
