"""Tests for the ExecutionProfile SSOT (issue #70).

`resolve_profile` is pure (fake env only, no I/O) and MUST source its brain
fields from the same `select_brain` code path `run`/`review --fix` execute, so
the displayed profile can never diverge from what actually runs. `render_lines`
and `to_dict` are the single formatting SSOT every outlet (doctor, startup
headers, JSON reports) reuses — these are golden tests.
"""
from __future__ import annotations

import pytest
from kagura_brain import BRAIN_API_KEY_ENV

from kagura_engineer.config import Config, ConfigError
from kagura_engineer.profile import (
    REVIEW_VIA_BRAIN,
    ExecutionProfile,
    ReviewProfile,
    render_lines,
    resolve_profile,
    resolve_review_profile,
    review_render_line,
    review_to_dict_or_none,
    to_dict,
)
from tests._constants import (
    VALID_CONTEXT_UUID,
    VALID_MEMORY_URL,
    VALID_PROFILE,
    VALID_WORKSPACE,
)


def _cloud_cfg(**overrides) -> Config:
    data = dict(
        profile=VALID_PROFILE,
        memory_cloud_url=VALID_MEMORY_URL,
        workspace_id=VALID_WORKSPACE,
        context_id=VALID_CONTEXT_UUID,
    )
    data.update(overrides)
    return Config(**data)


# --- resolve_profile ---------------------------------------------------------


def test_resolve_claude_default(tmp_path):
    prof = resolve_profile(_cloud_cfg(), {}, tmp_path)
    assert prof.brain_backend == "claude"
    assert prof.brain_endpoint is None
    assert prof.brain_mcp is True
    assert prof.reviewer_model is None
    assert prof.ollama_url == "http://localhost:11434"
    assert prof.memory_backend == "cloud"
    assert prof.workspace_id == VALID_WORKSPACE
    assert prof.context_id == VALID_CONTEXT_UUID
    assert prof.memory_mcp_config is None
    assert prof.memory_failover is True


def test_resolve_codex_with_endpoint(tmp_path):
    cfg = _cloud_cfg(brain_backend="codex", brain_endpoint="ollama-cloud")
    prof = resolve_profile(cfg, {BRAIN_API_KEY_ENV: "k"}, tmp_path)
    assert prof.brain_backend == "codex"
    assert prof.brain_endpoint == "ollama-cloud"
    # The engineer's in-task-MCP POLICY for codex defaults to off (issue #68),
    # even though the library handle is MCP-capable.
    assert prof.brain_mcp is False


def test_resolve_codex_mcp_policy_divergence(tmp_path):
    # enable_codex_mcp flips the POLICY field — the profile must reflect the
    # shim's `supports_mcp`, never the library handle's capability flag.
    cfg = _cloud_cfg(brain_backend="codex", enable_codex_mcp=True)
    prof = resolve_profile(cfg, {}, tmp_path)
    assert prof.brain_mcp is True


def test_resolve_half_pair_raises_config_error(tmp_path):
    # The codex half-configured-pair behaviour is select_brain's; resolve_profile
    # must preserve it (endpoint without key → ConfigError), not swallow it.
    cfg = _cloud_cfg(brain_endpoint="https://gw.example")
    with pytest.raises(ConfigError):
        resolve_profile(cfg, {}, tmp_path)


def test_resolve_brain_fields_come_from_select_brain(tmp_path, monkeypatch):
    # SSOT guarantee: the brain fields are read off the select_brain result,
    # not re-derived from cfg — patching select_brain must change the profile.
    import kagura_engineer.profile as mod

    class _Call:
        backend = "sentinel-backend"
        supports_mcp = False

    monkeypatch.setattr(mod, "select_brain", lambda cfg, env: _Call(), raising=True)
    prof = resolve_profile(_cloud_cfg(), {}, tmp_path)
    assert prof.brain_backend == "sentinel-backend"
    assert prof.brain_mcp is False


def test_resolve_local_backend_zeroes_cloud_fields(tmp_path):
    # A local-backend repo.yaml may still carry stale cloud ids; the profile
    # must not display them as if a run would use them.
    cfg = Config(
        profile=VALID_PROFILE, memory_backend="local",
        workspace_id="stale-ws", context_id="stale-ctx",
    )
    prof = resolve_profile(cfg, {}, tmp_path)
    assert prof.memory_backend == "local"
    assert prof.workspace_id == ""
    assert prof.context_id == ""


def test_resolve_discovers_generated_mcp_json(tmp_path):
    (tmp_path / ".mcp.json").write_text('{"mcpServers": {}}')
    prof = resolve_profile(_cloud_cfg(), {}, tmp_path)
    assert prof.memory_mcp_config == str(tmp_path / ".mcp.json")


def test_resolve_reviewer_model_is_first_review_model(tmp_path):
    cfg = _cloud_cfg(review={"models": ["qwen3-coder:480b", "other"]})
    prof = resolve_profile(cfg, {}, tmp_path)
    assert prof.reviewer_model == "qwen3-coder:480b"


# --- render_lines / to_dict (formatting SSOT — golden) -----------------------


def _profile(**overrides) -> ExecutionProfile:
    data = dict(
        brain_backend="claude",
        brain_endpoint=None,
        brain_mcp=True,
        reviewer_model="qwen3-coder:480b",
        ollama_url="http://localhost:11434",
        memory_backend="cloud",
        workspace_id="ws_xxx",
        context_id="ea753f42",
        memory_mcp_config=".mcp.json",
        memory_failover=True,
    )
    data.update(overrides)
    return ExecutionProfile(**data)


def test_render_lines_golden_cloud():
    assert render_lines(_profile()) == [
        "brain: claude (endpoint: default, in-task MCP: on)",
        "reviewer: qwen3-coder:480b @ http://localhost:11434",
        "memory: cloud · workspace=ws_xxx · context=ea753f42 · failover=on · mcp=.mcp.json",
    ]


def test_render_lines_golden_codex_endpoint_defaults():
    lines = render_lines(_profile(
        brain_backend="codex", brain_endpoint="ollama-cloud", brain_mcp=False,
        reviewer_model=None, memory_mcp_config=None, memory_failover=False,
    ))
    assert lines == [
        "brain: codex (endpoint: ollama-cloud, in-task MCP: off)",
        "reviewer: default @ http://localhost:11434",
        "memory: cloud · workspace=ws_xxx · context=ea753f42 · failover=off · mcp=none",
    ]


def test_render_lines_local_backend():
    lines = render_lines(_profile(
        memory_backend="local", workspace_id="", context_id="",
        memory_mcp_config=None,
    ))
    assert lines[-1] == "memory: local"


def test_render_lines_can_omit_brain_line():
    # `review` without --fix runs no brain — its header must not imply one.
    lines = render_lines(_profile(), brain=False)
    assert lines[0].startswith("reviewer:")
    assert not any(line.startswith("brain:") for line in lines)


# --- ReviewProfile (issue #74) -----------------------------------------------


class _FakeBrainCall:
    def __init__(self, backend):
        self.backend = backend


def test_resolve_review_profile_claude_default_endpoint():
    # run/goal delegate code review to the brain's in-phase /code-review, so the
    # reviewer IS the resolved brain backend. A None endpoint renders as default.
    rp = resolve_review_profile(_FakeBrainCall("claude"), None)
    assert rp == ReviewProfile(provider="claude", model=None, via=REVIEW_VIA_BRAIN)


def test_resolve_review_profile_carries_brain_endpoint_as_model():
    # The brain endpoint is the only model-identifying info knowable for the
    # delegated path (e.g. an ollama-cloud gateway via codex) — record it.
    rp = resolve_review_profile(_FakeBrainCall("codex"), "ollama-cloud")
    assert rp.provider == "codex"
    assert rp.model == "ollama-cloud"


def test_resolve_review_profile_reads_backend_off_the_brain_call():
    # SSOT: provider must come from the resolved BrainCall the phases execute
    # with, never re-derived — so it can never diverge from what ran.
    rp = resolve_review_profile(_FakeBrainCall("sentinel-backend"), None)
    assert rp.provider == "sentinel-backend"


def test_review_render_line_none_is_explicit():
    # issue #74 AC3: a run where no code review ran is distinguishable.
    assert review_render_line(None) == "review: none ran"


def test_review_render_line_golden():
    assert (
        review_render_line(ReviewProfile(provider="claude", model=None))
        == "review: claude @ default (via brain in-phase /code-review)"
    )
    assert (
        review_render_line(ReviewProfile(provider="codex", model="ollama-cloud"))
        == "review: codex @ ollama-cloud (via brain in-phase /code-review)"
    )


def test_review_to_dict_or_none_golden():
    assert review_to_dict_or_none(None) is None
    assert review_to_dict_or_none(ReviewProfile(provider="claude", model=None)) == {
        "provider": "claude",
        "model": None,
        "via": REVIEW_VIA_BRAIN,
    }


def test_to_dict_golden():
    assert to_dict(_profile()) == {
        "brain_backend": "claude",
        "brain_endpoint": None,
        "brain_mcp": True,
        "reviewer_model": "qwen3-coder:480b",
        "ollama_url": "http://localhost:11434",
        "memory_backend": "cloud",
        "workspace_id": "ws_xxx",
        "context_id": "ea753f42",
        "memory_mcp_config": ".mcp.json",
        "memory_failover": True,
    }
