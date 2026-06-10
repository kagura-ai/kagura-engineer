import logging

import pytest

import kagura_brain
from kagura_brain.core import BrainResult

from kagura_engineer.config import Config, ConfigError
from kagura_engineer.mcp import MEMORY_TOOLS
from kagura_engineer.run.brain_select import BrainCall, select_brain


def _cfg(**over) -> Config:
    base = {"profile": "p", "memory_backend": "local"}
    base.update(over)
    return Config.model_validate(base)


class _FakeHandle:
    """Stand-in for kagura_brain.BrainHandle that records invoke kwargs.

    `supports_mcp=True` mirrors kagura_brain 0.4.0, which reports BOTH backends as
    MCP-capable — the engineer shim must NOT trust that for codex (issue #63).
    """

    def __init__(self, backend, *, endpoint=None, api_key=None):
        self.backend = backend
        self.endpoint = endpoint
        self.api_key = api_key
        self.supports_mcp = True
        self.invoked = None

    def invoke(self, prompt, **kwargs):
        self.invoked = kwargs
        return "RESULT"


@pytest.fixture
def spy_select(monkeypatch):
    """Patch kagura_brain.select; record its args and expose the handle returned."""
    rec = {}

    def _fake(backend="claude", *, endpoint=None, api_key=None):
        rec["call"] = {"backend": backend, "endpoint": endpoint, "api_key": api_key}
        rec["handle"] = _FakeHandle(backend, endpoint=endpoint, api_key=api_key)
        return rec["handle"]

    monkeypatch.setattr(kagura_brain, "select", _fake)
    return rec


def test_default_is_claude_via_library_select_with_mcp_tools(spy_select):
    call = select_brain(_cfg(), env={})
    assert call.backend == "claude"
    assert call.supports_mcp is True
    # dispatch is delegated to the library, not hand-mapped here
    assert spy_select["call"] == {"backend": "claude", "endpoint": None, "api_key": None}
    call.invoke("hi", cwd=None, timeout=1, mcp_config="/x/.mcp.json")
    assert spy_select["handle"].invoked["mcp_config"] == "/x/.mcp.json"
    assert spy_select["handle"].invoked["allowed_tools"] == MEMORY_TOOLS


def test_codex_supports_mcp_false_and_no_mcp_kwargs_despite_library_capability(spy_select):
    # kagura_brain 0.4.0 reports codex as MCP-capable, but the engineer shim keeps
    # codex at no-in-task-MCP (behavior preserved from #51 — enabling it is a
    # separate change). The shim overrides the library capability and forwards
    # neither mcp_config nor allowed_tools.
    call = select_brain(_cfg(brain_backend="codex"), env={})
    assert call.backend == "codex"
    assert call.supports_mcp is False
    assert spy_select["handle"].supports_mcp is True  # library says yes...
    call.invoke("hi", cwd=None, timeout=1, mcp_config="/x/.mcp.json")
    assert "mcp_config" not in spy_select["handle"].invoked  # ...shim says no
    assert "allowed_tools" not in spy_select["handle"].invoked


def test_mcp_enabled_is_false_for_codex_even_with_config(spy_select):
    claude_call = select_brain(_cfg(), env={})
    codex_call = select_brain(_cfg(brain_backend="codex"), env={})
    assert claude_call.mcp_enabled("/x/.mcp.json") is True
    assert codex_call.mcp_enabled("/x/.mcp.json") is False
    assert claude_call.mcp_enabled(None) is False


def test_endpoint_and_api_key_passed_to_library_select(spy_select):
    select_brain(
        _cfg(brain_backend="codex", brain_endpoint="ollama-cloud"),
        env={"KAGURA_BRAIN_API_KEY": "sk-test"},
    )
    # endpoint/api_key now flow through select() → handle, not the shim's invoke
    assert spy_select["call"]["endpoint"] == "ollama-cloud"
    assert spy_select["call"]["api_key"] == "sk-test"


def test_endpoint_without_api_key_raises_before_select(spy_select):
    with pytest.raises(ConfigError, match="KAGURA_BRAIN_API_KEY"):
        select_brain(_cfg(brain_endpoint="ollama-cloud"), env={})
    assert "call" not in spy_select  # ConfigError raised before reaching the library


def test_subscription_claude_needs_no_api_key(spy_select):
    select_brain(_cfg(), env={})
    assert spy_select["call"]["api_key"] is None
    assert spy_select["call"]["endpoint"] is None


def test_codex_keeps_engineer_no_mcp_warning(spy_select, caplog):
    # The library is pure (no logging); the engineer keeps the operator signal that
    # codex runs ground out-of-band only.
    with caplog.at_level(logging.WARNING):
        select_brain(_cfg(brain_backend="codex"), env={})
    assert any("codex" in r.message.lower() for r in caplog.records)


# --- real-BrainHandle contract guard (NOT spy_select) -------------------------
# The tests above fake `kagura_brain.select`, so they cannot catch the shim
# drifting from the REAL handle's signature. These drive select_brain through the
# real `kagura_brain.select()` + `BrainHandle`, faking ONLY the leaf adapter
# (`kagura_brain.{claude,codex}.invoke` — no subprocess), and assert the shim's
# kwargs actually reach the adapter via the real handle. A library signature
# change (renamed mcp_config, dropped endpoint forwarding, …) breaks these.


def _capture_adapter(monkeypatch, backend):
    cap = {}

    def _invoke(prompt, **kwargs):
        cap["prompt"] = prompt
        cap.update(kwargs)
        return BrainResult(0, "", "", timed_out=False)

    monkeypatch.setattr(getattr(kagura_brain, backend), "invoke", _invoke)
    return cap


def test_real_handle_claude_forwards_mcp_tools_and_byo_creds_to_adapter(monkeypatch):
    cap = _capture_adapter(monkeypatch, "claude")
    call = select_brain(
        _cfg(brain_endpoint="https://gw"), env={"KAGURA_BRAIN_API_KEY": "sk-x"},
    )
    call.invoke("hi", cwd=None, timeout=5, mcp_config="/x/.mcp.json")
    assert cap["mcp_config"] == "/x/.mcp.json"
    assert tuple(cap["allowed_tools"]) == MEMORY_TOOLS
    assert cap["endpoint"] == "https://gw"   # BYO endpoint reaches the adapter
    assert cap["api_key"] == "sk-x"          # via the real handle, not the shim


def test_real_handle_codex_sends_no_live_mcp_to_adapter(monkeypatch):
    cap = _capture_adapter(monkeypatch, "codex")
    call = select_brain(_cfg(brain_backend="codex"), env={})
    call.invoke("hi", cwd=None, timeout=5, mcp_config="/x/.mcp.json")
    # The shim withholds mcp_config/tools for codex; through the real handle the
    # adapter therefore sees no live config and an empty tool set.
    assert cap.get("mcp_config") is None
    assert tuple(cap.get("allowed_tools", ())) == ()
