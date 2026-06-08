"""Memory Cloud client for the `run` agent loop.

`MemoryClient` is the narrow Protocol the orchestrator depends on — the
methods the loop needs (load_pinned / recall / recall_detailed / remember /
feedback / get_state / set_state). `KaguraCloudClient` wraps the `kagura-memory`
SDK's `KaguraClient` and normalizes its dict responses into the simple
shapes the loop wants (recall/load_pinned → list[str] of summaries,
get_state → the stored value or None).

Two impls are anticipated (design doc §5): this `KaguraCloudClient` now,
a `LocalMemoryClient` (SQLite, offline) in Plan 5. Keeping the Protocol
narrow means tests use an in-memory fake and never touch the network.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Protocol, runtime_checkable

from ..config import Config


@runtime_checkable
class MemoryClient(Protocol):
    def load_pinned(self, context_id: str) -> list[str]: ...
    def recall(
        self, context_id: str, query: str, *, k: int = 5,
        tags: list[str] | None = None, min_importance: float = 0.0,
    ) -> list[str]: ...
    # Like recall, but returns (memory_id, summary) pairs so the caller can
    # reinforce the memories it actually used via feedback().
    def recall_detailed(
        self, context_id: str, query: str, *, k: int = 5,
        tags: list[str] | None = None, min_importance: float = 0.0,
    ) -> list[tuple[str, str]]: ...
    def remember(
        self, context_id: str, *, summary: str, content: str, type: str,
        tags: list[str] | None = None,
    ) -> str: ...
    # Reinforce a memory that proved useful (Hebbian-style). `weight` scales
    # the reinforcement; the implementation decides how it is applied.
    def feedback(self, context_id: str, memory_id: str, *, weight: float = 1.0) -> None: ...
    # Pin / unpin a memory so load_pinned surfaces it (delivery_mode toggle).
    def pin(self, context_id: str, memory_id: str) -> None: ...
    def unpin(self, context_id: str, memory_id: str) -> None: ...
    # Graph discovery from a seed memory → related (memory_id, summary) pairs.
    def explore(
        self, context_id: str, memory_id: str, *, depth: int = 1
    ) -> list[tuple[str, str]]: ...
    def get_state(self, context_id: str, key: str) -> dict | None: ...
    def set_state(self, context_id: str, key: str, value: dict) -> None: ...


# Recalls that influence what the agent does are behaviour-influencing
# reads; the trusted tier excludes external/connector-ingested memories
# (OWASP LLM01/LLM03), matching the session-start bootstrap policy.
_TRUST_FILTER = {"trust_tier": "trusted"}


def _recall_filters(tags: list[str] | None, min_importance: float) -> dict:
    """Build the SDK recall filters: always trust-tier filtered, plus optional
    tag (match-any) and importance floor."""
    filters: dict = dict(_TRUST_FILTER)
    if tags:
        filters["tags"] = list(tags)
    if min_importance > 0.0:
        filters["importance"] = {"gte": min_importance}
    return filters


def _mcp_url(url: str) -> str:
    """Normalise the configured root URL into the SDK's ``mcp_url``.

    `kagura_memory.KaguraClient` treats ``mcp_url`` as the literal MCP endpoint
    (it only strips a trailing slash, then derives the base from it). Our config
    carries the *root* (e.g. ``https://memory.kagura-ai.com``, the same value
    doctor probes at ``/health``), so append ``/mcp`` — idempotently, so a value
    that already ends in ``/mcp`` (with or without a trailing slash) is left
    alone. Without this the SDK is handed the bare root and 4xx/405s every call.
    """
    if not url:
        return url
    stripped = url.rstrip("/")
    return stripped if stripped.endswith("/mcp") else f"{stripped}/mcp"


class KaguraCloudClient:
    """Adapter over `kagura_memory.KaguraClient`.

    The SDK is fully **async** (every method is a coroutine). The orchestrator's
    `MemoryClient` Protocol is sync, so this adapter bridges the two with a single
    persistent event loop (issue #1): the SDK's `httpx.AsyncClient` binds to the
    loop on first await, so every call must run on the *same* loop — a per-call
    `asyncio.run()` would spin up and tear down a fresh loop each time and fail
    the second call with "Event loop is closed". Bridge only at the outermost SDK
    call (never call one bridged method from another, or `run_until_complete`
    re-enters and raises). Call `close()` when done to release the loop + SDK.
    """

    def __init__(self, sdk: Any) -> None:
        self._sdk = sdk
        self._loop = asyncio.new_event_loop()

    def _run(self, coro: Any) -> Any:
        """The single sync→async bridge: drive one SDK coroutine to completion
        on the persistent loop."""
        return self._loop.run_until_complete(coro)

    def close(self) -> None:
        """Best-effort teardown: close the SDK's async resources (if it exposes
        an async ``close``), then the loop. Each step is guarded so one failure
        does not skip the next, and the loop close is idempotent."""
        try:
            closer = getattr(self._sdk, "close", None)
            if closer is not None and not self._loop.is_closed():
                self._run(closer())
        except Exception:  # noqa: BLE001 — teardown must never raise
            pass
        finally:
            if not self._loop.is_closed():
                self._loop.close()

    @classmethod
    def from_config(cls, cfg: Config) -> "KaguraCloudClient":
        import kagura_memory

        # api_key=None (env unset) lets the SDK fall back to its OAuth profile
        # (`kagura auth login`) — do not force an empty string. mcp_url is the
        # normalised endpoint (see _mcp_url).
        sdk = kagura_memory.KaguraClient(
            api_key=os.environ.get("KAGURA_API_KEY"),
            mcp_url=_mcp_url(cfg.memory_cloud_url),
        )
        return cls(sdk)

    def load_pinned(self, context_id: str) -> list[str]:
        resp = self._run(self._sdk.load_pinned(context_id))
        return [m["summary"] for m in resp.get("memories", []) if m.get("summary")]

    def recall(
        self, context_id: str, query: str, *, k: int = 5,
        tags: list[str] | None = None, min_importance: float = 0.0,
    ) -> list[str]:
        # Grounding-only: summaries are useful even for an id-less row, so this
        # keeps a looser filter than recall_detailed (which needs ids for feedback).
        resp = self._run(self._sdk.recall(
            context_id, query=query, k=k,
            filters=_recall_filters(tags, min_importance),
        ))
        return [r["summary"] for r in resp.get("results", []) if r.get("summary")]

    def recall_detailed(
        self, context_id: str, query: str, *, k: int = 5,
        tags: list[str] | None = None, min_importance: float = 0.0,
    ) -> list[tuple[str, str]]:
        resp = self._run(self._sdk.recall(
            context_id, query=query, k=k,
            filters=_recall_filters(tags, min_importance),
        ))
        return [
            (r["memory_id"], r["summary"])
            for r in resp.get("results", [])
            if r.get("summary") and r.get("memory_id")
        ]

    def feedback(self, context_id: str, memory_id: str, *, weight: float = 1.0) -> None:
        # Cloud SDK speaks the MCP `feedback` contract: an append-only usefulness
        # signal (helpful: bool), not a neural weight. A positive reinforcement
        # weight maps to helpful=True. (issue #16)
        self._run(self._sdk.feedback(context_id, memory_id, helpful=weight > 0))

    def pin(self, context_id: str, memory_id: str) -> None:
        self._run(self._sdk.update_memory(context_id, memory_id=memory_id, delivery_mode="always"))

    def unpin(self, context_id: str, memory_id: str) -> None:
        self._run(self._sdk.update_memory(context_id, memory_id=memory_id, delivery_mode="on_recall"))

    def explore(
        self, context_id: str, memory_id: str, *, depth: int = 1
    ) -> list[tuple[str, str]]:
        # SDK passthrough to the Hebbian-graph explore. Defensive parse: the
        # response surfaces related nodes under "nodes" or "results".
        resp = self._run(self._sdk.explore(context_id, memory_id=memory_id, depth=depth))
        nodes = resp.get("nodes") or resp.get("results") or []
        return [
            (n["memory_id"], n["summary"])
            for n in nodes
            if n.get("memory_id") and n.get("summary")
        ]

    def remember(
        self, context_id: str, *, summary: str, content: str, type: str,
        tags: list[str] | None = None,
    ) -> str:
        resp = self._run(self._sdk.remember(
            context_id, summary=summary, content=content, type=type, tags=tags
        ))
        return resp.get("memory_id", "")

    def get_state(self, context_id: str, key: str) -> dict | None:
        resp = self._run(self._sdk.get_state(context_id, key))
        if not resp:
            return None
        return resp.get("value")

    def set_state(self, context_id: str, key: str, value: dict) -> None:
        self._run(self._sdk.set_state(context_id, key, value))


def resolve_memory_client(cfg: Config) -> MemoryClient:
    """Pick the memory backend from config: ``local`` → the offline SQLite
    ``LocalMemoryClient`` (no network, no API key); anything else → the Kagura
    Memory Cloud SDK client. The orchestrators call this for their default
    (non-injected) memory client so the backend is one config switch away."""
    if cfg.memory_backend == "local":
        from .local_memory import LocalMemoryClient

        return LocalMemoryClient(cfg.local_memory_path)
    return KaguraCloudClient.from_config(cfg)
