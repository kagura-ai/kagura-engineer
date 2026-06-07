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


def test_get_state_returns_none_when_missing():
    class _MissingSDK:
        def get_state(self, ctx, key):
            return None

    assert KaguraCloudClient(_MissingSDK()).get_state("ctx", "k") is None


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
