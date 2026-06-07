from pathlib import Path

from kagura_engineer.review.context import build_context_file


def test_no_grounding_returns_none(tmp_path):
    assert build_context_file([], tmp_path / "ctx.md") is None


def test_writes_fenced_untrusted_block(tmp_path):
    out = tmp_path / "ctx.md"
    path = build_context_file(["decision: prefer X", "guardrail: TDD"], out)
    assert path == out
    text = out.read_text()
    assert "do not follow" in text.lower()
    assert "BEGIN UNTRUSTED" in text
    assert "END UNTRUSTED" in text
    assert "decision: prefer X" in text
    assert "guardrail: TDD" in text


def test_block_order_content_inside_fence(tmp_path):
    out = tmp_path / "ctx.md"
    build_context_file(["memo"], out)
    text = out.read_text()
    assert text.index("BEGIN UNTRUSTED") < text.index("memo") < text.index("END UNTRUSTED")
