import subprocess
from pathlib import Path

from kagura_engineer.review import fixer
from kagura_engineer.review.fixer import FixerResult
from kagura_engineer.review.result import Finding


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


def test_run_fixer_invokes_claude_in_repo(monkeypatch, tmp_path):
    seen = {}

    def _fake_run(cmd, **kw):
        seen["cmd"] = cmd
        seen["cwd"] = kw.get("cwd")
        return subprocess.CompletedProcess(cmd, 0, "fixed and committed", "")

    monkeypatch.setattr(fixer.subprocess, "run", _fake_run)
    res = fixer.run_fixer(tmp_path, "do the fix")
    assert isinstance(res, FixerResult)
    assert seen["cmd"][0] == "claude" and "-p" in seen["cmd"]
    assert "do the fix" in seen["cmd"]
    assert seen["cwd"] == tmp_path
    assert res.returncode == 0
    assert res.timed_out is False


def test_run_fixer_nonzero_keeps_output(monkeypatch, tmp_path):
    monkeypatch.setattr(
        fixer.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "boom"),
    )
    res = fixer.run_fixer(tmp_path, "p")
    assert res.returncode == 1
    assert "boom" in res.stderr


def test_run_fixer_timeout_decodes_bytes(monkeypatch, tmp_path):
    def _raise(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1, output=b"partial\n", stderr=b"warn")

    monkeypatch.setattr(fixer.subprocess, "run", _raise)
    res = fixer.run_fixer(tmp_path, "p")
    assert res.timed_out is True
    assert res.returncode == -1
    assert isinstance(res.stdout, str) and res.stdout == "partial\n"
    assert isinstance(res.stderr, str) and res.stderr == "warn"
