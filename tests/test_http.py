"""The HTTP probes must send a custom User-Agent.

Root cause (verified live 2026-06-09): Cloudflare blocks the stdlib default
``Python-urllib/x.y`` UA with HTTP 403 (CF error 1010) in front of the Memory
Cloud host, so `doctor`/`setup` reported the host as unreachable. Any non-default
UA passes. These tests pin that every outbound probe carries our UA — never the
stdlib default — so the regression cannot come back silently.
"""

from __future__ import annotations

import urllib.request


class _FakeResp:
    def __init__(self, body: bytes = b"{}") -> None:
        self._body = body

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


def test_build_request_sets_custom_user_agent():
    from kagura_engineer._http import USER_AGENT, build_request

    req = build_request("https://memory.example.com/health")
    ua = req.get_header("User-agent")
    assert ua == USER_AGENT
    assert ua.startswith("kagura-engineer/")
    assert "Python-urllib" not in ua


def test_doctor_probes_send_custom_ua(monkeypatch):
    import kagura_engineer.doctor.checks as checks

    seen: list[str | None] = []

    def fake_urlopen(req, timeout=None):  # noqa: ANN001
        # The fix passes a urllib.request.Request (has get_header); the bug
        # passed a bare str (no get_header) — which fails this capture loudly.
        seen.append(req.get_header("User-agent"))
        return _FakeResp(b"{}")

    monkeypatch.setattr(checks.urllib.request, "urlopen", fake_urlopen)

    checks._http_reach("https://memory.example.com/health")
    checks._http_json("https://memory.example.com/api/tags")

    assert seen, "urlopen was not called"
    for ua in seen:
        assert ua and "Python-urllib" not in ua


def test_setup_memory_cloud_probe_sends_custom_ua(monkeypatch):
    import kagura_engineer.setup.memory_cloud as mc

    seen: list[str | None] = []

    def fake_urlopen(req, timeout=None):  # noqa: ANN001
        seen.append(req.get_header("User-agent"))
        return _FakeResp(b"")

    monkeypatch.setattr(mc.urllib.request, "urlopen", fake_urlopen)

    # Reach the /health probe with a credential-less env so the call is exercised.
    mc.ensure_memory_cloud_reachable(
        base_url="https://memory.example.com",
        no_input=True,
        dry_run=False,
        env={},
        home=None,
    )

    assert seen, "the /health probe did not call urlopen"
    for ua in seen:
        assert ua and "Python-urllib" not in ua
