"""Memory Cloud client for the `run` agent loop.

`MemoryClient` is the narrow Protocol the orchestrator depends on. Its
``bootstrap`` method is the session-start seam: Cloud maps it to one
``KaguraClient.get_agent_bootstrap`` call, while offline implementations compose
the same lanes locally. The remaining primitive methods support review,
exploration, persistence, and feedback.

Two impls are anticipated (design doc §5): this `KaguraCloudClient` now,
a `LocalMemoryClient` (SQLite, offline) in Plan 5. Keeping the Protocol
narrow means tests use an in-memory fake and never touch the network.
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from ..config import Config

BootstrapComponent = Literal["pinned", "recall", "upcoming", "state", "policy"]
BOOTSTRAP_COMPONENTS: tuple[BootstrapComponent, ...] = (
    "pinned",
    "recall",
    "upcoming",
    "state",
    "policy",
)


class BootstrapContractError(RuntimeError):
    """The server returned an unsafe or internally inconsistent bootstrap."""


@dataclass(frozen=True)
class MemoryBootstrap:
    """Normalized, model-safe session-start bundle.

    Recall retains ``(memory_id, summary)`` pairs so the existing feedback loop
    reinforces exactly the memories that influenced the run. Transport and
    trace identifiers other than the explicit agent/session correlation stay
    out of the model-visible grounding.
    """

    agent_id: str | None = None
    context_id: str | None = None
    session_id: str | None = None
    instructions: str = ""
    pinned: tuple[str, ...] = ()
    recalled: tuple[tuple[str, str], ...] = ()
    upcoming: tuple[str, ...] = ()
    state: dict[str, Any] = field(default_factory=dict)
    degraded: bool = False
    component_statuses: tuple[tuple[str, str], ...] = ()

    @property
    def failed_components(self) -> tuple[str, ...]:
        return tuple(
            name for name, status in self.component_statuses if status == "error"
        )


@runtime_checkable
class MemoryClient(Protocol):
    def bootstrap(
        self,
        context_id: str,
        *,
        agent_id: str | None,
        session_id: str,
        query: str | None,
        k: int = 5,
        include: list[BootstrapComponent] | None = None,
        state_key: str | None = None,
    ) -> MemoryBootstrap: ...
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
    # Reinforce a memory that proved useful (Hebbian-style). Contract (issue #21):
    # `weight > 0` reinforces — its magnitude is honored best-effort and is
    # backend-dependent (the local backend scales an importance bump by it; the
    # cloud backend is boolean and ignores magnitude). `weight <= 0` means "no
    # reinforcement" and is a NO-OP on every backend: the harness only ever
    # reinforces useful memories, so no backend records negative feedback.
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


def compose_bootstrap(
    client: MemoryClient,
    context_id: str,
    *,
    agent_id: str | None,
    session_id: str,
    query: str | None,
    k: int = 5,
    include: list[BootstrapComponent] | None = None,
    state_key: str | None = None,
) -> MemoryBootstrap:
    """Client-side composition fallback for an offline memory implementation.

    Each lane is fail-soft like the server envelope. Upcoming memories and
    policy bundles have no local representation, so they are explicit skipped
    components rather than silently missing data.
    """
    if not 1 <= k <= 100:
        raise ValueError("bootstrap k must be in [1, 100]")
    wanted = set(include or BOOTSTRAP_COMPONENTS)
    unknown = wanted.difference(BOOTSTRAP_COMPONENTS)
    if unknown:
        raise ValueError(f"unknown bootstrap components: {sorted(unknown)}")

    pinned: tuple[str, ...] = ()
    recalled: tuple[tuple[str, str], ...] = ()
    state: dict[str, Any] = {}
    statuses: list[tuple[str, str]] = []

    if "pinned" in wanted:
        try:
            pinned = tuple(client.load_pinned(context_id))
        except Exception:  # noqa: BLE001 - preserve healthy sibling components
            statuses.append(("pinned", "error"))
        else:
            statuses.append(("pinned", "ok"))
    if "recall" in wanted:
        if query is None:
            statuses.append(("recall", "skipped"))
        else:
            try:
                recalled = tuple(client.recall_detailed(context_id, query, k=k))
            except Exception:  # noqa: BLE001 - preserve healthy sibling components
                statuses.append(("recall", "error"))
            else:
                statuses.append(("recall", "ok"))
    if "upcoming" in wanted:
        statuses.append(("upcoming", "skipped"))
    if "state" in wanted:
        try:
            value = client.get_state(context_id, state_key) if state_key else None
        except Exception:  # noqa: BLE001 - preserve healthy sibling components
            statuses.append(("state", "error"))
        else:
            if state_key and value is not None:
                state[state_key] = value
            statuses.append(("state", "ok"))
    if "policy" in wanted:
        statuses.append(("policy", "skipped"))

    failed = any(status == "error" for _, status in statuses)
    return MemoryBootstrap(
        agent_id=agent_id,
        context_id=context_id,
        session_id=session_id,
        pinned=pinned,
        recalled=recalled,
        state=state,
        degraded=failed,
        component_statuses=tuple(statuses),
    )


def _as_mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        value = model_dump(mode="json")
    if not isinstance(value, Mapping):
        raise BootstrapContractError(f"bootstrap {label} is not an object")
    return value


def _memory_summaries(component: Mapping[str, Any], key: str) -> tuple[str, ...]:
    rows = component.get(key, [])
    if not isinstance(rows, list):
        raise BootstrapContractError(f"bootstrap {key} lane is not an array")
    summaries: list[str] = []
    for row in rows:
        item = _as_mapping(row, label=f"{key} memory")
        summary = item.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise BootstrapContractError(f"bootstrap {key} memory has no summary")
        summaries.append(summary)
    return tuple(summaries)


def _recalled_memories(component: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    rows = component.get("results", [])
    if not isinstance(rows, list):
        raise BootstrapContractError("bootstrap recall results are not an array")
    recalled: list[tuple[str, str]] = []
    for row in rows:
        item = _as_mapping(row, label="recall memory")
        memory_id = item.get("memory_id")
        summary = item.get("summary")
        if not isinstance(memory_id, str) or not memory_id.strip():
            raise BootstrapContractError("bootstrap recall memory has no memory_id")
        if not isinstance(summary, str) or not summary.strip():
            raise BootstrapContractError("bootstrap recall memory has no summary")
        recalled.append((memory_id, summary))
    return tuple(recalled)


def normalize_cloud_bootstrap(
    response: Any,
    *,
    requested_agent_id: str,
    requested_context_id: str,
    requested_session_id: str,
    include: list[BootstrapComponent] | None,
) -> MemoryBootstrap:
    """Validate and normalize the SDK's Pydantic bootstrap response."""
    raw = _as_mapping(response, label="response")
    if raw.get("status") != "success":
        raise BootstrapContractError("bootstrap top-level status is not success")
    degraded = raw.get("degraded")
    if not isinstance(degraded, bool):
        raise BootstrapContractError("bootstrap degraded flag is not boolean")

    agent = _as_mapping(raw.get("agent"), label="agent")
    context = _as_mapping(raw.get("context"), label="context")
    correlation = _as_mapping(raw.get("correlation"), label="correlation")
    resolved_agent_id = agent.get("agent_id")
    resolved_context_id = context.get("id")
    resolved_session_id = correlation.get("session_id")
    if resolved_agent_id != requested_agent_id:
        raise BootstrapContractError("bootstrap resolved outside the configured agent")
    if resolved_context_id != requested_context_id:
        raise BootstrapContractError("bootstrap resolved outside the configured context")
    if resolved_session_id != requested_session_id:
        raise BootstrapContractError("bootstrap returned a different session correlation")
    binding = agent.get("binding")
    if binding is not None:
        binding_map = _as_mapping(binding, label="agent binding")
        if binding_map.get("context_id") != requested_context_id:
            raise BootstrapContractError("bootstrap binding disagrees with context identity")

    instructions = raw.get("instructions")
    if instructions is not None and not isinstance(instructions, str):
        raise BootstrapContractError("bootstrap instructions are malformed")
    components = _as_mapping(raw.get("components"), label="components")
    expected = include or list(BOOTSTRAP_COMPONENTS)
    statuses: list[tuple[str, str]] = []
    parsed: dict[str, Mapping[str, Any]] = {}
    for name in expected:
        component = _as_mapping(components.get(name), label=f"{name} component")
        status = component.get("status")
        if status not in {"ok", "error", "skipped"}:
            raise BootstrapContractError(f"bootstrap {name} status is invalid")
        parsed[name] = component
        statuses.append((name, status))
    failed = tuple(name for name, status in statuses if status == "error")
    if degraded != bool(failed):
        raise BootstrapContractError("bootstrap degraded flag disagrees with components")

    recall_component = parsed.get("recall")
    if recall_component and recall_component.get("status") == "ok":
        if recall_component.get("trust_filter") != "trusted":
            raise BootstrapContractError("bootstrap recall is not proven trusted-only")

    pinned_component = parsed.get("pinned")
    upcoming_component = parsed.get("upcoming")
    state_component = parsed.get("state")
    pinned = (
        _memory_summaries(pinned_component, "memories")
        if pinned_component and pinned_component.get("status") == "ok"
        else ()
    )
    recalled = (
        _recalled_memories(recall_component)
        if recall_component and recall_component.get("status") == "ok"
        else ()
    )
    upcoming = (
        _memory_summaries(upcoming_component, "results")
        if upcoming_component and upcoming_component.get("status") == "ok"
        else ()
    )
    raw_state = state_component.get("states", {}) if state_component else {}
    if state_component and state_component.get("status") == "ok" and not isinstance(
        raw_state, Mapping
    ):
        raise BootstrapContractError("bootstrap state lane is not an object")

    return MemoryBootstrap(
        agent_id=resolved_agent_id,
        context_id=resolved_context_id,
        session_id=resolved_session_id,
        instructions=instructions or "",
        pinned=pinned,
        recalled=recalled,
        upcoming=upcoming,
        state=dict(raw_state) if isinstance(raw_state, Mapping) else {},
        degraded=degraded,
        component_statuses=tuple(statuses),
    )


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

    def bootstrap(
        self,
        context_id: str,
        *,
        agent_id: str | None,
        session_id: str,
        query: str | None,
        k: int = 5,
        include: list[BootstrapComponent] | None = None,
        state_key: str | None = None,
    ) -> MemoryBootstrap:
        """Call the server's one-round-trip agent session bootstrap."""
        del state_key  # the Cloud state component returns all live state keys
        if not agent_id:
            raise BootstrapContractError("cloud bootstrap requires config.agent_id")
        response = self._run(
            self._sdk.get_agent_bootstrap(
                agent_id,
                context_id=context_id,
                session_id=session_id,
                query=query,
                recall_k=k,
                include=include,
            )
        )
        return normalize_cloud_bootstrap(
            response,
            requested_agent_id=agent_id,
            requested_context_id=context_id,
            requested_session_id=session_id,
            include=include,
        )

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
        # Contract (issue #21): weight > 0 reinforces; weight <= 0 is "no
        # reinforcement" → no-op. We never send helpful=False: the harness only
        # reinforces useful memories (no "penalize" caller), and a non-positive
        # weight must not record active negative feedback on the cloud — that
        # would also diverge from the local backend's no-op.
        if weight <= 0:
            return
        # The verified kagura-memory 0.37 SDK is helpful-based, not weight-based:
        #   feedback(context_id, memory_id, helpful, *, query=None, note=None)
        # so a positive reinforcement weight maps to helpful=True (issue #16).
        # Passing `weight=` raised TypeError, silently killing cloud reinforcement
        # once per recalled memory; the offline fake (tests/run/test_memory.py)
        # mirrors the REAL signature so a regression back to `weight=` fails CI.
        self._run(self._sdk.feedback(context_id, memory_id, helpful=True))

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
    Memory Cloud SDK client, wrapped in a ``FailoverMemoryClient`` (unless
    ``memory_failover`` is off) so critical writes survive a Cloud outage. The
    orchestrators call this for their default (non-injected) memory client so the
    backend is one config switch away."""
    if cfg.memory_backend == "local":
        from .local_memory import LocalMemoryClient

        return LocalMemoryClient(cfg.local_memory_path)
    cloud = KaguraCloudClient.from_config(cfg)
    if not cfg.memory_failover:
        return cloud
    from .failover_memory import FailoverMemoryClient, default_wal_path

    return FailoverMemoryClient(cloud, default_wal_path(cfg.context_id))
