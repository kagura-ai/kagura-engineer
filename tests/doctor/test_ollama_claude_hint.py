"""check_ollama must not tell users to `ollama pull <claude-model>`.

`review.models` are Ollama model names for the reviewer. A Claude model name
(haiku/sonnet/opus/claude-*) there is a config mistake — `ollama pull haiku`
would fail, so doctor should guide rather than suggest it.
"""

from __future__ import annotations

import kagura_engineer.doctor.checks as checks
from kagura_engineer.doctor.result import Status


def _patch_tags(monkeypatch, names: list[str]) -> None:
    monkeypatch.setattr(
        checks,
        "_http_json",
        lambda url: {"models": [{"name": n} for n in names]},
    )


def test_claude_model_name_in_review_models_is_not_an_ollama_pull(monkeypatch):
    _patch_tags(monkeypatch, [])  # ollama has no models
    res = checks.check_ollama("http://x", required=["haiku"])
    assert res.status is Status.WARN
    assert "ollama pull haiku" not in (res.fix_hint or "")
    assert "Claude model" in (res.fix_hint or "")


def test_mixed_missing_keeps_ollama_pull_for_real_models(monkeypatch):
    _patch_tags(monkeypatch, [])
    res = checks.check_ollama("http://x", required=["haiku", "qwen3-coder"])
    hint = res.fix_hint or ""
    assert res.status is Status.WARN
    assert "Claude model" in hint
    assert "ollama pull qwen3-coder" in hint  # real ollama model still gets a pull hint
    assert "ollama pull haiku" not in hint


def test_real_missing_ollama_model_still_suggests_pull(monkeypatch):
    _patch_tags(monkeypatch, [])
    res = checks.check_ollama("http://x", required=["qwen3-coder"])
    assert res.status is Status.WARN
    assert "ollama pull qwen3-coder" in (res.fix_hint or "")


def test_looks_like_claude_model():
    assert checks._looks_like_claude_model("haiku")
    assert checks._looks_like_claude_model("Sonnet")
    assert checks._looks_like_claude_model("claude-3-5-haiku")
    assert not checks._looks_like_claude_model("qwen3-coder")
    assert not checks._looks_like_claude_model("gpt-oss:20b")
