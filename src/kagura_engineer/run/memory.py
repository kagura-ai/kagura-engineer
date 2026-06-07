"""Memory Cloud client for the `run` agent loop.

`MemoryClient` is the narrow Protocol the orchestrator depends on — just
the five methods the loop needs (recall / load_pinned / remember /
get_state / set_state). `KaguraCloudClient` wraps the `kagura-memory`
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
    def recall(self, context_id: str, query: str, *, k: int = 5) -> list[str]: ...
    def remember(
        self, context_id: str, *, summary: str, content: str, type: str,
        tags: list[str] | None = None,
    ) -> str: ...
    def get_state(self, context_id: str, key: str) -> dict | None: ...
    def set_state(self, context_id: str, key: str, value: dict) -> None: ...


# Recalls that influence what the agent does are behaviour-influencing
# reads; the trusted tier excludes external/connector-ingested memories
# (OWASP LLM01/LLM03), matching the session-start bootstrap policy.
_TRUST_FILTER = {"trust_tier": "trusted"}


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

    def recall(self, context_id: str, query: str, *, k: int = 5) -> list[str]:
        resp = self._sdk.recall(context_id, query=query, k=k, filters=_TRUST_FILTER)
        return [r["summary"] for r in resp.get("results", []) if r.get("summary")]

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
