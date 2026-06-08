"""`ensure_memory_cloud_reachable` step: reachability probe + credential gate.

The step does two cheap, local-or-network things:

  1. Resolves the Memory Cloud credential (`KAGURA_API_KEY` env, or a
     `kagura auth login` OAuth profile) via `resolve_memory_cloud_auth`.
  2. GETs `{base_url}/health` to confirm the host is reachable.

A reachable host with **no** resolvable credential is reported as
`NEEDS_USER` (not OK) — that is the issue #6 footgun: setup used to pass on
reachability alone, then `run` would die at the first cloud call with
`api_key=None`. `NEEDS_USER` is exactly the bucket the result model
reserves for "ask the user to log in", and `--no-input` treats it as
blocking. The hint names both supported fixes (`export KAGURA_API_KEY=...`
and `kagura auth login`), matching what `run/memory.py` actually consumes.

The full authed recall smoke (a live API round-trip) is still out of scope
here — this gate confirms a credential *resolves*, not that it is valid.
"""
from __future__ import annotations

import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from .._http import build_request

from .memory_auth import MemoryAuthMethod, resolve_memory_cloud_auth
from .result import StepResult, StepStatus

_PROBE_TIMEOUT_S = 5

# Canonical fix for a missing credential — names both supported sources
# (env key + `kagura auth login`), env-first, matching run/memory.py.
_MEMORY_AUTH_HINT = (
    "export KAGURA_API_KEY=... or run `kagura auth login` to authenticate Memory Cloud"
)


def _host_only(url: str) -> str:
    # `urlparse(...).hostname` drops username:password@ automatically
    # so we do not echo credentials into the doctor/setup detail.
    try:
        return urlparse(url).hostname or url
    except (ValueError, TypeError):
        return url


def ensure_memory_cloud_reachable(
    base_url: str,
    *,
    no_input: bool,
    dry_run: bool,
    env: dict[str, str] | None = None,
    home: Path | None = None,
) -> StepResult:
    name = "memory-cloud"
    started = time.monotonic()
    host = _host_only(base_url)

    if dry_run:
        return StepResult(
            name,
            StepStatus.OK,
            f"dry-run: would GET {host}/health",
            duration_s=time.monotonic() - started,
        )

    # Resolve the credential before reporting OK: a reachable host with no
    # credential is NEEDS_USER, not OK (issue #6). env/home are injectable
    # for tests; production reads os.environ / Path.home() via the resolver.
    auth = resolve_memory_cloud_auth(env=env, home=home)
    has_credential = auth.method is not MemoryAuthMethod.NONE

    url = f"{base_url.rstrip('/')}/health"
    try:
        # build_request sets a User-Agent — Cloudflare 403s the stdlib default (see _http.py).
        with urllib.request.urlopen(build_request(url), timeout=_PROBE_TIMEOUT_S) as resp:
            # 2xx is the success path; we do not currently parse the
            # body (the doctor's reachability probe is body-agnostic,
            # and setup mirrors that).
            _ = resp.read()
        if not has_credential:
            return StepResult(
                name,
                StepStatus.NEEDS_USER,
                f"reachable at {host}, but no Memory Cloud credential resolves",
                fix_hint=_MEMORY_AUTH_HINT,
                duration_s=time.monotonic() - started,
            )
        return StepResult(
            name,
            StepStatus.OK,
            f"reachable at {host} (auth={auth.detail})",
            duration_s=time.monotonic() - started,
        )
    except urllib.error.HTTPError as exc:
        # An HTTP response (even 4xx/5xx) proves the host is reachable. A 4xx is
        # often the auth layer rejecting an absent credential, so when none
        # resolves we ask the user to authenticate rather than passing as OK.
        if not has_credential:
            return StepResult(
                name,
                StepStatus.NEEDS_USER,
                f"reachable but /health returned HTTP {exc.code}; no credential resolves",
                fix_hint=_MEMORY_AUTH_HINT,
                duration_s=time.monotonic() - started,
            )
        return StepResult(
            name,
            StepStatus.OK,
            f"reachable but /health returned HTTP {exc.code} (auth={auth.detail})",
            fix_hint="a live recall round-trip will confirm the credential is valid",
            duration_s=time.monotonic() - started,
        )
    except (urllib.error.URLError, OSError) as exc:
        return StepResult(
            name,
            StepStatus.FAIL,
            f"unreachable: {exc}",
            fix_hint="check config.memory_cloud_url / network",
            duration_s=time.monotonic() - started,
        )
    except ValueError as exc:
        # json.JSONDecodeError / malformed URL — treat as FAIL.
        return StepResult(
            name,
            StepStatus.FAIL,
            f"probe failed: {type(exc).__name__}: {exc}",
            fix_hint="check config.memory_cloud_url",
            duration_s=time.monotonic() - started,
        )
