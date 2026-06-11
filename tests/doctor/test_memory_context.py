"""Tests for the `memory-context` doctor check (issue #70).

The wrong-context-recall incident detector: doctor live-resolves
`cfg.context_id` via the memory SDK's `get_context_info` and shows the
context *name*, so a wildcard/stale binding pointing at the wrong context is
visible pre-flight. The fetch boundary is injectable; the fakes return REAL
`kagura_memory.models.ContextInfo` objects so the parse stays signature-true
to the SDK (the test-fake-fidelity lesson from issues #1/#16).
"""
from __future__ import annotations

from kagura_memory.models import ContextDetail, ContextInfo

from kagura_engineer.config import Config
from kagura_engineer.doctor import checks
from kagura_engineer.doctor.result import Status
from tests._constants import (
    VALID_CONTEXT_UUID,
    VALID_MEMORY_URL,
    VALID_PROFILE,
    VALID_WORKSPACE,
)


def _cfg() -> Config:
    return Config(
        profile=VALID_PROFILE,
        memory_cloud_url=VALID_MEMORY_URL,
        workspace_id=VALID_WORKSPACE,
        context_id=VALID_CONTEXT_UUID,
    )


def _info(display_name="kagura-engineer Development", name="kagura-engineer-dev"):
    return ContextInfo(
        context=ContextDetail(
            id=VALID_CONTEXT_UUID, name=name, display_name=display_name
        )
    )


def test_memory_context_ok_shows_resolved_name():
    res = checks.check_memory_context(_cfg(), fetch=lambda cfg: _info())
    assert res.name == "memory-context"
    assert res.status is Status.OK
    assert f'context {VALID_CONTEXT_UUID} → "kagura-engineer Development"' in res.detail


def test_memory_context_ok_falls_back_to_name_without_display_name():
    res = checks.check_memory_context(
        _cfg(), fetch=lambda cfg: _info(display_name=None)
    )
    assert res.status is Status.OK
    assert '"kagura-engineer-dev"' in res.detail


def test_memory_context_unresolvable_id_fails_with_config_hint():
    # The past-incident detector: an id that does not resolve (or belongs to
    # another workspace) must FAIL and point at config.context_id.
    def _not_found(cfg):
        raise RuntimeError("context_not_found: no access")

    res = checks.check_memory_context(_cfg(), fetch=_not_found)
    assert res.status is Status.FAIL
    assert "context_not_found" in res.detail
    assert "config.context_id" in (res.fix_hint or "")


def test_memory_context_network_error_degrades_to_fail_never_raises():
    def _down(cfg):
        raise ConnectionError("connection refused")

    res = checks.check_memory_context(_cfg(), fetch=_down)  # must not raise
    assert res.status is Status.FAIL
    assert "connection refused" in res.detail


def test_memory_context_nameless_response_fails():
    # A response that resolves but carries no usable name is still a FAIL —
    # the operator cannot confirm the context identity from it.
    res = checks.check_memory_context(
        _cfg(), fetch=lambda cfg: _info(display_name=None, name="")
    )
    assert res.status is Status.FAIL
    assert "config.context_id" in (res.fix_hint or "")
