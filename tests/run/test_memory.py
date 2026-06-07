from kagura_engineer.run.memory import KaguraCloudClient, MemoryClient


class _FakeSDK:
    """Stand-in for kagura_memory.KaguraClient with recorded calls."""

    def __init__(self):
        self.calls = []

    def recall(self, context_id, query="", k=5, filters=None, **kw):
        self.calls.append(("recall", context_id, query, k, filters))
        return {"results": [{"summary": "past decision A"}, {"summary": "pattern B"}, {"no_summary": 1}]}

    def load_pinned(self, context_id, cap=None):
        self.calls.append(("load_pinned", context_id))
        return {"memories": [{"summary": "guardrail: TDD required"}]}

    def remember(self, context_id, summary, content, type="note", **kw):
        self.calls.append(("remember", context_id, summary, type))
        return {"memory_id": "mem-123"}

    def get_state(self, context_id, key=None):
        self.calls.append(("get_state", context_id, key))
        return {"value": {"phase": "start"}}

    def set_state(self, context_id, key, value, **kw):
        self.calls.append(("set_state", context_id, key, value))
        return {"ok": True}


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
