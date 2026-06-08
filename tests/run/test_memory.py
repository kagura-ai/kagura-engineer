from kagura_engineer.run.memory import KaguraCloudClient, MemoryClient


class _FakeSDK:
    """Stand-in for ``kagura_memory.KaguraClient`` — an **async** SDK: every
    method is a coroutine, mirroring kagura_memory 0.29 (issue #1). Modelling the
    real contract is the point: a sync fake (the previous version) hid the
    sync/async mismatch that left the whole cloud path dead."""

    def __init__(self):
        self.calls = []
        self.closed = False

    async def recall(self, context_id, query="", k=5, filters=None, **kw):
        self.calls.append(("recall", context_id, query, k, filters))
        return {"results": [{"summary": "past decision A"}, {"summary": "pattern B"}, {"no_summary": 1}]}

    async def load_pinned(self, context_id, cap=None):
        self.calls.append(("load_pinned", context_id))
        return {"memories": [{"summary": "guardrail: TDD required"}]}

    async def remember(self, context_id, summary, content, type="note", **kw):
        self.calls.append(("remember", context_id, summary, type))
        return {"memory_id": "mem-123"}

    async def get_state(self, context_id, key=None):
        self.calls.append(("get_state", context_id, key))
        return {"value": {"phase": "start"}}

    async def set_state(self, context_id, key, value, **kw):
        self.calls.append(("set_state", context_id, key, value))
        return {"ok": True}

    async def close(self):
        self.closed = True


def test_recall_returns_summary_strings_and_skips_missing():
    sdk = _FakeSDK()
    client = KaguraCloudClient(sdk)
    out = client.recall("ctx", "issue 42 context", k=3)
    assert out == ["past decision A", "pattern B"]
    name, ctx, query, k, filters = sdk.calls[-1]
    assert ctx == "ctx" and k == 3
    assert filters == {"trust_tier": "trusted"}


def test_load_pinned_returns_summary_strings():
    client = KaguraCloudClient(_FakeSDK())
    assert client.load_pinned("ctx") == ["guardrail: TDD required"]


def test_remember_returns_memory_id():
    client = KaguraCloudClient(_FakeSDK())
    mid = client.remember("ctx", summary="s", content="c", type="savepoint")
    assert mid == "mem-123"


def test_get_state_unwraps_value():
    client = KaguraCloudClient(_FakeSDK())
    assert client.get_state("ctx", "run:42") == {"phase": "start"}


def test_set_state_passes_value():
    sdk = _FakeSDK()
    KaguraCloudClient(sdk).set_state("ctx", "run:42", {"done": True})
    assert sdk.calls[-1] == ("set_state", "ctx", "run:42", {"done": True})


def test_kagura_cloud_client_satisfies_protocol():
    client: MemoryClient = KaguraCloudClient(_FakeSDK())
    assert isinstance(client, MemoryClient)  # runtime_checkable


def test_get_state_returns_none_when_missing():
    class _MissingSDK:
        async def get_state(self, ctx, key):
            return None

    assert KaguraCloudClient(_MissingSDK()).get_state("ctx", "k") is None


# --- issue #1: the bridge must AWAIT the async SDK -------------------------


def test_bridge_awaits_async_sdk_not_returns_coroutine():
    # Regression for #1: the methods called the async SDK synchronously, so
    # `resp` was a coroutine and `resp.get(...)` raised — the whole cloud path
    # was dead. The bridge must run the coroutine to completion and parse the
    # resolved dict.
    out = KaguraCloudClient(_FakeSDK()).recall("ctx", "q")
    assert out == ["past decision A", "pattern B"]


def test_calls_run_on_one_persistent_loop():
    # The SDK's httpx.AsyncClient binds to the loop on first await; every call
    # must run on the SAME loop, so a second call after the first must not fail
    # with "Event loop is closed" (the per-call asyncio.run() failure mode).
    c = KaguraCloudClient(_FakeSDK())
    assert c.recall("ctx", "q1") == ["past decision A", "pattern B"]
    assert c.load_pinned("ctx") == ["guardrail: TDD required"]  # 2nd call, same loop


def test_close_closes_sdk_and_loop():
    sdk = _FakeSDK()
    c = KaguraCloudClient(sdk)
    c.recall("ctx", "q")
    c.close()
    assert sdk.closed is True
    assert c._loop.is_closed()


def test_close_is_safe_without_sdk_close_and_is_idempotent():
    class _NoClose:
        async def recall(self, *a, **k):
            return {"results": []}

    c = KaguraCloudClient(_NoClose())
    c.close()  # SDK has no close() → still closes the loop, no raise
    c.close()  # idempotent
    assert c._loop.is_closed()


# --- issue #1: mcp_url normalisation --------------------------------------


def test_mcp_url_appends_mcp_idempotently():
    from kagura_engineer.run.memory import _mcp_url

    assert _mcp_url("https://memory.kagura-ai.com") == "https://memory.kagura-ai.com/mcp"
    assert _mcp_url("https://memory.kagura-ai.com/") == "https://memory.kagura-ai.com/mcp"
    assert _mcp_url("https://memory.kagura-ai.com/mcp") == "https://memory.kagura-ai.com/mcp"
    assert _mcp_url("https://memory.kagura-ai.com/mcp/") == "https://memory.kagura-ai.com/mcp"
    assert _mcp_url("") == ""


# --- Plan 5: backend factory ----------------------------------------------


def _cfg_backend(backend, tmp_path):
    from kagura_engineer.config import Config
    return Config(profile="t", memory_cloud_url="http://x", workspace_id="w",
                  context_id="c", memory_backend=backend,
                  local_memory_path=str(tmp_path / "mem.db"))


def test_resolve_memory_client_local(tmp_path):
    from kagura_engineer.run.memory import resolve_memory_client
    from kagura_engineer.run.local_memory import LocalMemoryClient
    client = resolve_memory_client(_cfg_backend("local", tmp_path))
    assert isinstance(client, LocalMemoryClient)


def test_resolve_memory_client_cloud(monkeypatch, tmp_path):
    from kagura_engineer.run import memory as mem_mod
    sentinel = object()
    monkeypatch.setattr(mem_mod.KaguraCloudClient, "from_config",
                        classmethod(lambda cls, cfg: sentinel))
    assert mem_mod.resolve_memory_client(_cfg_backend("cloud", tmp_path)) is sentinel


def test_invalid_memory_backend_raises_config_error(tmp_path):
    import pytest
    from kagura_engineer.config import ConfigError, load_config
    cfg = tmp_path / "repo.yaml"
    cfg.write_text(
        "profile: t\nmemory_cloud_url: http://x\nworkspace_id: w\n"
        "context_id: c\nmemory_backend: bogus\n"
    )
    with pytest.raises(ConfigError):
        load_config(str(cfg))


# --- Plan 5+: recall_detailed + feedback on the cloud client ---------------


def test_cloud_recall_detailed_returns_pairs_and_recall_wraps():
    class _Sdk:
        async def recall(self, context_id, *, query, k, filters):
            return {"results": [{"memory_id": "a", "summary": "S1"}, {"summary": "no-id"}]}

    c = KaguraCloudClient(_Sdk())
    assert c.recall_detailed("ctx", "q") == [("a", "S1")]  # needs id → drops id-less row
    assert c.recall("ctx", "q") == ["S1", "no-id"]  # summary-only → keeps both


def test_cloud_feedback_maps_weight_to_helpful():
    # The real kagura-memory 0.29 SDK is helpful-based, NOT weight-based:
    #   feedback(context_id, memory_id, helpful, *, query=None, note=None)
    # The cloud adapter must map the Protocol's positive `weight` onto
    # `helpful=True` and pass NO `weight` kwarg (issue #16). This fake mirrors
    # the real signature so a regression back to `weight=` fails here.
    seen = {}

    class _Sdk:
        async def feedback(self, context_id, memory_id, helpful, *, query=None, note=None):
            seen.update(
                context_id=context_id, memory_id=memory_id,
                helpful=helpful, query=query, note=note,
            )

    KaguraCloudClient(_Sdk()).feedback("ctx", "m1", weight=2.0)
    assert seen == {
        "context_id": "ctx", "memory_id": "m1",
        "helpful": True, "query": None, "note": None,
    }


def test_cloud_feedback_positive_weight_passes_helpful_true():
    # weight > 0 always reinforces → helpful=True (magnitude is best-effort and
    # discarded on cloud, which is boolean). issue #21.
    seen = {}

    class _Sdk:
        async def feedback(self, context_id, memory_id, helpful, *, query=None, note=None):
            seen["helpful"] = helpful

    KaguraCloudClient(_Sdk()).feedback("ctx", "m1", weight=0.25)
    assert seen["helpful"] is True


def test_cloud_feedback_nonpositive_weight_is_noop():
    # Contract (issue #21): weight <= 0 means "no reinforcement" → no-op. The
    # cloud adapter must NOT call the SDK at all (it must never record active
    # negative feedback, which would also diverge from the local no-op).
    calls = []

    class _Sdk:
        async def feedback(self, context_id, memory_id, helpful, *, query=None, note=None):
            calls.append(helpful)

    for weight in (0.0, -1.0):
        KaguraCloudClient(_Sdk()).feedback("ctx", "m1", weight=weight)
    assert calls == [], "non-positive weight must not call the SDK"


# --- Plan 5+: recall filters + pin/unpin on the cloud client ----------------


def test_cloud_recall_passes_tag_and_importance_filters():
    seen = {}

    class _Sdk:
        async def recall(self, context_id, *, query, k, filters):
            seen["filters"] = filters
            return {"results": []}

    KaguraCloudClient(_Sdk()).recall("ctx", "q", tags=["security"], min_importance=0.7)
    assert seen["filters"]["trust_tier"] == "trusted"
    assert seen["filters"]["tags"] == ["security"]
    assert seen["filters"]["importance"] == {"gte": 0.7}


def test_cloud_recall_no_filters_is_trust_only():
    seen = {}

    class _Sdk:
        async def recall(self, context_id, *, query, k, filters):
            seen["filters"] = filters
            return {"results": []}

    KaguraCloudClient(_Sdk()).recall("ctx", "q")
    assert seen["filters"] == {"trust_tier": "trusted"}  # no tags/importance keys


def test_cloud_pin_unpin_passthrough():
    calls = []

    class _Sdk:
        async def update_memory(self, context_id, *, memory_id, delivery_mode):
            calls.append((memory_id, delivery_mode))

    c = KaguraCloudClient(_Sdk())
    c.pin("ctx", "m1")
    c.unpin("ctx", "m1")
    assert calls == [("m1", "always"), ("m1", "on_recall")]


def test_cloud_explore_passthrough_and_parse():
    seen = {}

    class _Sdk:
        async def explore(self, context_id, *, memory_id, depth):
            seen.update(memory_id=memory_id, depth=depth)
            return {"nodes": [{"memory_id": "a", "summary": "A"}, {"summary": "no-id"}]}

    out = KaguraCloudClient(_Sdk()).explore("ctx", "seed", depth=2)
    assert seen == {"memory_id": "seed", "depth": 2}
    assert out == [("a", "A")]  # id-less node dropped
