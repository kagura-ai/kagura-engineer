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


class KaguraCloudClient:
    """Adapter over `kagura_memory.KaguraClient`."""

    def __init__(self, sdk: Any) -> None:
        self._sdk = sdk

    @classmethod
    def from_config(cls, cfg: Config) -> "KaguraCloudClient":
        import kagura_memory

        sdk = kagura_memory.KaguraClient(
            api_key=os.environ.get("KAGURA_API_KEY"),
            mcp_url=cfg.memory_cloud_url,
        )
        return cls(sdk)

    def load_pinned(self, context_id: str) -> list[str]:
        resp = self._sdk.load_pinned(context_id)
        return [m["summary"] for m in resp.get("memories", []) if m.get("summary")]

    def recall(
        self, context_id: str, query: str, *, k: int = 5,
        tags: list[str] | None = None, min_importance: float = 0.0,
    ) -> list[str]:
        # Grounding-only: summaries are useful even for an id-less row, so this
        # keeps a looser filter than recall_detailed (which needs ids for feedback).
        resp = self._sdk.recall(
            context_id, query=query, k=k,
            filters=_recall_filters(tags, min_importance),
        )
        return [r["summary"] for r in resp.get("results", []) if r.get("summary")]

    def recall_detailed(
        self, context_id: str, query: str, *, k: int = 5,
        tags: list[str] | None = None, min_importance: float = 0.0,
    ) -> list[tuple[str, str]]:
        resp = self._sdk.recall(
            context_id, query=query, k=k,
            filters=_recall_filters(tags, min_importance),
        )
        return [
            (r["memory_id"], r["summary"])
            for r in resp.get("results", [])
            if r.get("summary") and r.get("memory_id")
        ]

    def feedback(self, context_id: str, memory_id: str, *, weight: float = 1.0) -> None:
        # SDK passthrough — reinforce the memory's neural weight. Not exercised
        # by the offline test suite (the SDK isn't a declared dependency); the
        # contract mirrors the mcp `feedback` tool.
        self._sdk.feedback(context_id, memory_id=memory_id, weight=weight)

    def pin(self, context_id: str, memory_id: str) -> None:
        self._sdk.update_memory(context_id, memory_id=memory_id, delivery_mode="always")

    def unpin(self, context_id: str, memory_id: str) -> None:
        self._sdk.update_memory(context_id, memory_id=memory_id, delivery_mode="on_recall")

    def explore(
        self, context_id: str, memory_id: str, *, depth: int = 1
    ) -> list[tuple[str, str]]:
        # SDK passthrough to the Hebbian-graph explore. Defensive parse: the
        # response surfaces related nodes under "nodes" or "results".
        resp = self._sdk.explore(context_id, memory_id=memory_id, depth=depth)
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
        resp = self._sdk.remember(
            context_id, summary=summary, content=content, type=type, tags=tags
        )
        return resp.get("memory_id", "")

    def get_state(self, context_id: str, key: str) -> dict | None:
        resp = self._sdk.get_state(context_id, key)
        if not resp:
            return None
        return resp.get("value")

    def set_state(self, context_id: str, key: str, value: dict) -> None:
        self._sdk.set_state(context_id, key, value)


def resolve_memory_client(cfg: Config) -> MemoryClient:
    """Pick the memory backend from config: ``local`` → the offline SQLite
    ``LocalMemoryClient`` (no network, no API key); anything else → the Kagura
    Memory Cloud SDK client. The orchestrators call this for their default
    (non-injected) memory client so the backend is one config switch away."""
    if cfg.memory_backend == "local":
        from .local_memory import LocalMemoryClient

        return LocalMemoryClient(cfg.local_memory_path)
    return KaguraCloudClient.from_config(cfg)
