from types import SimpleNamespace

from kagura_brain.core import BrainResult

from kagura_engineer.mcp import MEMORY_TOOLS
from kagura_engineer.review import fixer
from kagura_engineer.review.fixer import FixerResult, run_fixer
from kagura_engineer.review.result import Finding
from kagura_engineer.run.brain_select import BrainCall


def _fake_brain_call(stdout="", stderr="", returncode=0, timed_out=False, capture=None):
    # A claude-like BrainCall (supports_mcp=True) wrapping a kwargs-swallowing
    # fake invoke. BrainCall.invoke adds mcp_config + allowed_tools for MCP
    # backends, so `capture` sees what the adapter would actually receive.
    def _invoke(prompt, **kw):
        if capture is not None:
            capture["prompt"] = prompt
            capture.update(kw)
        return BrainResult(returncode, stdout, stderr, timed_out=timed_out)

    # The shim wraps a kagura_brain handle (invoke-only); the fake mirrors that
    # shape with a stub exposing `.invoke`.
    return BrainCall("fake-claude", SimpleNamespace(invoke=_invoke), supports_mcp=True)


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


def test_run_fixer_routes_through_harness_brain_in_repo(tmp_path):
    # run_fixer delegates to the resolved BrainCall (#51) — which wraps the
    # shared brain.invoke launcher (#40), same seam as run/workflow.py — and
    # maps the BrainResult onto a FixerResult.
    cap = {}
    res = fixer.run_fixer(
        tmp_path, "do the fix",
        brain_call=_fake_brain_call(stdout="fixed and committed", capture=cap),
    )
    assert isinstance(res, FixerResult)
    assert cap["prompt"] == "do the fix"
    assert cap["cwd"] == tmp_path
    assert tuple(cap["allowed_tools"]) == MEMORY_TOOLS
    assert res.returncode == 0
    assert res.timed_out is False


def test_run_fixer_nonzero_keeps_output(tmp_path):
    res = fixer.run_fixer(
        tmp_path, "p", brain_call=_fake_brain_call(returncode=1, stderr="boom"),
    )
    assert res.returncode == 1
    assert "boom" in res.stderr


def test_run_fixer_timeout_uses_detail_label(tmp_path):
    # The harness decodes partial bytes; run_fixer surfaces detail() on timeout.
    res = fixer.run_fixer(
        tmp_path, "p",
        brain_call=_fake_brain_call(
            returncode=-1, stdout="partial\n", stderr="warn", timed_out=True
        ),
    )
    assert res.timed_out is True
    assert res.returncode == -1
    assert res.stdout == "partial\n"
    assert res.stderr == "warn"


def test_run_fixer_timeout_no_output_falls_back_to_label(tmp_path):
    res = fixer.run_fixer(
        tmp_path, "p", brain_call=_fake_brain_call(returncode=-1, timed_out=True),
    )
    assert res.timed_out is True
    assert res.stderr == "timed out"


def test_build_fix_prompt_mcp_note_when_enabled():
    p = fixer.build_fix_prompt(None, _findings(), mcp_enabled=True)
    assert "mcp__kagura-memory__recall" in p


def test_run_fixer_forwards_mcp_config_to_brain(tmp_path):
    cap = {}
    fixer.run_fixer(
        tmp_path, "p", brain_call=_fake_brain_call(capture=cap), mcp_config="/tmp/m.json",
    )
    assert cap["mcp_config"] == "/tmp/m.json"


def test_run_fixer_uses_brain_call_and_omits_mcp_for_codex(tmp_path):
    records: list[dict] = []
    def _invoke(prompt, **kwargs):
        records.append(kwargs)
        class _R:
            returncode = 0
            stdout = ""
            stderr = ""
            timed_out = False
            def detail(self): return ""
        return _R()
    codex_call = BrainCall("fake-codex", SimpleNamespace(invoke=_invoke), supports_mcp=False)
    res = run_fixer(tmp_path, "fix it", mcp_config="/x/.mcp.json", brain_call=codex_call)
    assert res.returncode == 0
    assert "mcp_config" not in records[0]
    assert "allowed_tools" not in records[0]
