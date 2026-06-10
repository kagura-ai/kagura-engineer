import pytest

from kagura_engineer.config import Config, ConfigError
from kagura_engineer.mcp import MEMORY_TOOLS
from kagura_engineer.run.brain_select import BrainCall, select_brain


def _cfg(**over) -> Config:
    base = {"profile": "p", "memory_backend": "local"}
    base.update(over)
    return Config.model_validate(base)


class _Spy:
    """Records the kwargs an adapter.invoke received."""
    def __init__(self):
        self.kwargs = None
    def __call__(self, prompt, **kwargs):
        self.kwargs = kwargs
        return "RESULT"


def test_default_is_claude_with_mcp_tools():
    call = select_brain(_cfg(), env={})
    assert call.backend == "claude"
    assert call.supports_mcp is True
    spy = _Spy()
    object.__setattr__(call, "_invoke", spy)
    call.invoke("hi", cwd=None, timeout=1, mcp_config="/x/.mcp.json")
    assert spy.kwargs["mcp_config"] == "/x/.mcp.json"
    assert spy.kwargs["allowed_tools"] == MEMORY_TOOLS
    assert "endpoint" not in spy.kwargs


def test_codex_gets_no_mcp_kwargs():
    call = select_brain(_cfg(brain_backend="codex"), env={})
    assert call.backend == "codex"
    assert call.supports_mcp is False
    spy = _Spy()
    object.__setattr__(call, "_invoke", spy)
    call.invoke("hi", cwd=None, timeout=1, mcp_config="/x/.mcp.json")
    assert "mcp_config" not in spy.kwargs
    assert "allowed_tools" not in spy.kwargs


def test_mcp_enabled_is_false_for_codex_even_with_config():
    claude_call = select_brain(_cfg(), env={})
    codex_call = select_brain(_cfg(brain_backend="codex"), env={})
    assert claude_call.mcp_enabled("/x/.mcp.json") is True
    assert codex_call.mcp_enabled("/x/.mcp.json") is False
    assert claude_call.mcp_enabled(None) is False


def test_endpoint_passes_through_with_api_key_from_env():
    call = select_brain(
        _cfg(brain_backend="codex", brain_endpoint="ollama-cloud"),
        env={"KAGURA_BRAIN_API_KEY": "sk-test"},
    )
    spy = _Spy()
    object.__setattr__(call, "_invoke", spy)
    call.invoke("hi", cwd=None, timeout=1, mcp_config=None)
    assert spy.kwargs["endpoint"] == "ollama-cloud"
    assert spy.kwargs["api_key"] == "sk-test"


def test_endpoint_without_api_key_raises_configerror():
    with pytest.raises(ConfigError, match="KAGURA_BRAIN_API_KEY"):
        select_brain(_cfg(brain_endpoint="ollama-cloud"), env={})


def test_subscription_claude_needs_no_api_key():
    call = select_brain(_cfg(), env={})
    assert call.api_key is None
    assert call.endpoint is None
