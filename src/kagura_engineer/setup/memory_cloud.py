"""`ensure_memory_cloud_reachable` step: thin wrapper over doctor.

The setup step is intentionally narrower than the doctor check:
it is a *reachability* probe, not an auth check. The full authed
recall smoke (which actually exercises the API key) is Plan 3's
job — the setup step is the gate that says "the host is up and
will answer a GET, so the rest of the pipeline can try to talk to
it".

This file is the place to add the deeper check later, if
the project decides that setup should also probe the auth path.
For Plan 2 v1 we deliberately keep the surface minimal: this
step is a single HTTP GET, with the same reachability contract
as doctor (4xx = WARN, unreachable = FAIL).
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from .result import StepResult, StepStatus

_PROBE_TIMEOUT_S = 5


def _host_only(url: str) -> str:
    # `urlparse(...).hostname` drops username:password@ automatically
    # so we do not echo credentials into the doctor/setup detail.
    try:
        return urlparse(url).hostname or url
    except (ValueError, TypeError):
        return url


def ensure_memory_cloud_reachable(
    base_url: str, *, no_input: bool, dry_run: bool
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

    url = f"{base_url.rstrip('/')}/health"
    try:
        with urllib.request.urlopen(url, timeout=_PROBE_TIMEOUT_S) as resp:
            # 2xx is the success path; we do not currently parse the
            # body (the doctor's reachability probe is body-agnostic,
            # and setup mirrors that).
            _ = resp.read()
        return StepResult(
            name,
            StepStatus.OK,
            f"reachable at {host}",
            duration_s=time.monotonic() - started,
        )
    except urllib.error.HTTPError as exc:
        # An HTTP response (even 4xx/5xx) proves the host is reachable.
        # We report OK (not WARN) because setup's StepStatus enum has
        # no WARN bucket — OK is the right "host is up, this step's
        # job is done" outcome. The auth check itself is Plan 3; we
        # mention it in the detail so the operator knows where the
        # next gate is.
        return StepResult(
            name,
            StepStatus.OK,
            f"reachable but /health returned HTTP {exc.code}",
            fix_hint="auth/endpoint verification lands in Plan 3 (recall smoke)",
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
