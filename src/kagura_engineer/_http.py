"""Shared HTTP helpers for the lightweight stdlib probes (doctor / setup).

A custom ``User-Agent`` is **mandatory** for any request that may traverse
Cloudflare — notably the Memory Cloud host. Cloudflare blocks the stdlib default
``Python-urllib/x.y`` signature with HTTP 403 (CF error 1010), which made
`doctor`/`setup` report a perfectly healthy Memory Cloud as unreachable. Any
non-default UA passes, so every probe goes out as ``kagura-engineer/<version>``.

Verified live (2026-06-09) against ``https://memory.kagura-ai.com/health``:
``Python-urllib`` → 403, ``kagura-engineer/<ver>`` / ``curl`` / browser → 200.
"""

from __future__ import annotations

import urllib.request

from . import __version__

USER_AGENT = f"kagura-engineer/{__version__}"


def build_request(url: str) -> urllib.request.Request:
    """A GET ``Request`` carrying our ``User-Agent`` (never the stdlib default)."""
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
