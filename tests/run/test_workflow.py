import subprocess
from pathlib import Path

from kagura_engineer.run import workflow
from kagura_engineer.run.workflow import PhaseInvocation


def test_build_prompt_includes_command_grounding_and_marker_request():
    prompt = workflow.build_prompt("start", 42, ["guardrail: TDD", "decision A"])
    assert "/gh-issue-driven:start" in prompt
    assert "42" in prompt
    assert "guardrail: TDD" in prompt
    assert "KAGURA_VERDICT=" in prompt  # we instruct the session to emit the marker


def test_build_prompt_handles_empty_grounding():
    prompt = workflow.build_prompt("ship", 1, [])
    assert "/gh-issue-driven:ship" in prompt


def test_parse_verdict_reads_last_marker():
    text = "blah\nKAGURA_VERDICT=green\nmore\nKAGURA_VERDICT=red\n"
    assert workflow.parse_verdict(text) == "red"


def test_parse_verdict_returns_none_when_absent():
    assert workflow.parse_verdict("no marker here") is None


def test_parse_pr_url_reads_marker():
    assert workflow.parse_pr_url("KAGURA_PR_URL=https://github.com/o/r/pull/5\n") == "https://github.com/o/r/pull/5"


def test_parse_pr_url_none_when_absent_or_dash():
    assert workflow.parse_pr_url("KAGURA_PR_URL=-\n") is None
    assert workflow.parse_pr_url("nothing") is None


def test_invoke_phase_runs_claude_in_worktree_and_parses(monkeypatch, tmp_path):
    def _fake_run(cmd, **kw):
        assert cmd[0] == "claude" and "-p" in cmd
        assert kw["cwd"] == tmp_path
        return subprocess.CompletedProcess(
            cmd, 0, "work...\nKAGURA_VERDICT=green\nKAGURA_PR_URL=https://x/pull/1\n", ""
        )

    monkeypatch.setattr(workflow.subprocess, "run", _fake_run)
    inv = workflow.invoke_phase("ship", 3, tmp_path, ["g"])
    assert isinstance(inv, PhaseInvocation)
    assert inv.verdict == "green"
    assert inv.pr_url == "https://x/pull/1"
    assert inv.returncode == 0


def test_invoke_phase_nonzero_returncode_keeps_output(monkeypatch, tmp_path):
    monkeypatch.setattr(
        workflow.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "boom"),
    )
    inv = workflow.invoke_phase("start", 3, tmp_path, [])
    assert inv.returncode == 1
    assert inv.verdict is None
    assert "boom" in inv.stderr


def test_invoke_phase_timeout_returns_marker(monkeypatch, tmp_path):
    def _raise(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(workflow.subprocess, "run", _raise)
    inv = workflow.invoke_phase("start", 3, tmp_path, [])
    assert inv.returncode == -1
    assert inv.timed_out is True
