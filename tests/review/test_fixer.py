from kagura_brain.core import BrainResult

from kagura_engineer.mcp import MEMORY_TOOLS
from kagura_engineer.review import fixer
from kagura_engineer.review.fixer import FixerResult
from kagura_engineer.review.result import Finding


def _fake_brain(stdout="", stderr="", returncode=0, timed_out=False, capture=None):
    def _invoke(prompt, **kw):
        if capture is not None:
            capture["prompt"] = prompt
            capture.update(kw)
        return BrainResult(returncode, stdout, stderr, timed_out=timed_out)

    return _invoke


def _findings():
    return [
        Finding("security", "HIGH", "a.py", 3, "SQL injection"),
        Finding("correctness", "CRITICAL", "b.py", None, "off-by-one"),
    ]


def test_build_fix_prompt_points_to_report_and_lists_blocking():
    prompt = fixer.build_fix_prompt("/tmp/.kagura/review.json", _findings())
    assert "/tmp/.kagura/review.json" in prompt        # full detail source
    assert "SQL injection" in prompt and "a.py:3" in prompt
    assert "off-by-one" in prompt and "b.py" in prompt  # None line → bare file
    assert "commit" in prompt.lower()                   # must persist for re-review
    assert "push" in prompt.lower()                     # ... but not push


def test_build_fix_prompt_handles_no_report_path():
    prompt = fixer.build_fix_prompt(None, _findings())
    assert "SQL injection" in prompt  # still lists findings inline


def test_run_fixer_routes_through_harness_brain_in_repo(monkeypatch, tmp_path):
    # run_fixer delegates to the shared brain.invoke launcher (#40) — same seam
    # as run/workflow.py, so it inherits the #34 key-strip — and maps the
    # BrainResult onto a FixerResult.
    cap = {}
    monkeypatch.setattr(
        fixer.brain, "invoke",
        _fake_brain(stdout="fixed and committed", capture=cap),
    )
    res = fixer.run_fixer(tmp_path, "do the fix")
    assert isinstance(res, FixerResult)
    assert cap["prompt"] == "do the fix"
    assert cap["cwd"] == tmp_path
    assert tuple(cap["allowed_tools"]) == MEMORY_TOOLS
    assert res.returncode == 0
    assert res.timed_out is False


def test_run_fixer_nonzero_keeps_output(monkeypatch, tmp_path):
    monkeypatch.setattr(fixer.brain, "invoke", _fake_brain(returncode=1, stderr="boom"))
    res = fixer.run_fixer(tmp_path, "p")
    assert res.returncode == 1
    assert "boom" in res.stderr


def test_run_fixer_timeout_uses_detail_label(monkeypatch, tmp_path):
    # The harness decodes partial bytes; run_fixer surfaces detail() on timeout.
    monkeypatch.setattr(
        fixer.brain, "invoke",
        _fake_brain(returncode=-1, stdout="partial\n", stderr="warn", timed_out=True),
    )
    res = fixer.run_fixer(tmp_path, "p")
    assert res.timed_out is True
    assert res.returncode == -1
    assert res.stdout == "partial\n"
    assert res.stderr == "warn"


def test_run_fixer_timeout_no_output_falls_back_to_label(monkeypatch, tmp_path):
    monkeypatch.setattr(
        fixer.brain, "invoke", _fake_brain(returncode=-1, timed_out=True),
    )
    res = fixer.run_fixer(tmp_path, "p")
    assert res.timed_out is True
    assert res.stderr == "timed out"


def test_build_fix_prompt_mcp_note_when_enabled():
    p = fixer.build_fix_prompt(None, _findings(), mcp_enabled=True)
    assert "mcp__kagura-memory__recall" in p


def test_run_fixer_forwards_mcp_config_to_brain(monkeypatch, tmp_path):
    cap = {}
    monkeypatch.setattr(fixer.brain, "invoke", _fake_brain(capture=cap))
    fixer.run_fixer(tmp_path, "p", mcp_config="/tmp/m.json")
    assert cap["mcp_config"] == "/tmp/m.json"
