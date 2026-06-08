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


# --- native `## Verdict:` fallback (issue #2) ---------------------------------
# The delegated gh-issue-driven / c-suite skills close with a native
# `## Verdict: <green|yellow|red>` line — a shared, structured verdict token
# (emitted by c-suite, parsed by gh-issue-driven). When the model completes the
# skill but drops the harness's KAGURA_VERDICT= marker, fall back to that line
# rather than halting a healthy run. This is a *blessed secondary contract*, not
# a free-form scrape; the KAGURA_VERDICT= marker stays primary.


def test_parse_verdict_falls_back_to_native_line_when_marker_absent():
    text = "design review...\n\n## Verdict: green\n"
    assert workflow.parse_verdict(text) == "green"


def test_parse_verdict_marker_wins_over_native_line():
    # Both present → the explicit marker is authoritative even if a native
    # line disagrees and appears later in the text.
    text = "KAGURA_VERDICT=red\nblah\n## Verdict: green\n"
    assert workflow.parse_verdict(text) == "red"


def test_parse_verdict_native_line_is_case_insensitive():
    assert workflow.parse_verdict("## VERDICT: Yellow") == "yellow"


def test_parse_verdict_native_line_last_wins():
    # Escalation can emit several verdict lines; the final one is the decision.
    text = "## Verdict: red\nreconsidered\n## Verdict: green\n"
    assert workflow.parse_verdict(text) == "green"


def test_parse_verdict_native_decline_token_is_ignored():
    # `decline` is a c-suite routing token, not a phase verdict — the native
    # fallback only recognises green|yellow|red, so a decline-only body halts.
    assert workflow.parse_verdict("## Verdict: decline") is None


def test_parse_verdict_native_tolerates_trailing_punctuation():
    assert workflow.parse_verdict("## Verdict: green.") == "green"


def test_parse_verdict_native_tolerates_leading_whitespace():
    # Parity with gh-issue-driven's canonical `^\s*##\s*Verdict:` regex — an
    # indented/quoted verdict line (list item, blockquote) must still match.
    assert workflow.parse_verdict("  ## Verdict: green") == "green"


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
    assert inv.phase == "ship"


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


def test_invoke_phase_timeout_preserves_partial_output(monkeypatch, tmp_path):
    def _raise(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1, output="partial work\n", stderr="warn")

    monkeypatch.setattr(workflow.subprocess, "run", _raise)
    inv = workflow.invoke_phase("start", 3, tmp_path, [])
    assert inv.timed_out is True
    assert inv.stdout == "partial work\n"
    assert inv.stderr == "warn"


def test_invoke_phase_timeout_decodes_bytes_output(monkeypatch, tmp_path):
    # Real timeouts deliver bytes even under text=True; PhaseInvocation fields
    # are typed str and downstream parse_verdict does str ops — must be decoded.
    def _raise(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1, output=b"partial\n", stderr=b"warn")

    monkeypatch.setattr(workflow.subprocess, "run", _raise)
    inv = workflow.invoke_phase("start", 3, tmp_path, [])
    assert inv.timed_out is True
    assert isinstance(inv.stdout, str) and inv.stdout == "partial\n"
    assert isinstance(inv.stderr, str) and inv.stderr == "warn"


def test_build_prompt_unattended_adds_instruction():
    p = workflow.build_prompt("start", 1, [], unattended=True)
    assert "UNATTENDED" in p


def test_build_prompt_default_has_no_unattended():
    assert "UNATTENDED" not in workflow.build_prompt("start", 1, [])


def test_invoke_phase_forwards_unattended_into_prompt(monkeypatch, tmp_path):
    captured = {}

    def _run(cmd, **kw):
        captured["prompt"] = cmd[cmd.index("-p") + 1]
        return subprocess.CompletedProcess(cmd, 0, "KAGURA_VERDICT=green\n", "")

    monkeypatch.setattr(workflow.subprocess, "run", _run)
    workflow.invoke_phase("ship", 2, tmp_path, [], unattended=True)
    assert "UNATTENDED" in captured["prompt"]


def test_build_prompt_mcp_note_when_enabled():
    p = workflow.build_prompt("start", 1, [], mcp_enabled=True)
    assert "mcp__kagura-memory__recall" in p
    assert "UNTRUSTED" in p


def test_build_prompt_no_mcp_note_by_default():
    assert "mcp__kagura-memory" not in workflow.build_prompt("start", 1, [])


def test_invoke_phase_attaches_mcp_config(monkeypatch, tmp_path):
    cap = {}

    def _run(cmd, **kw):
        cap["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "KAGURA_VERDICT=green\n", "")

    monkeypatch.setattr(workflow.subprocess, "run", _run)
    workflow.invoke_phase("ship", 2, tmp_path, [], mcp_config="/tmp/m.json")
    assert "--mcp-config" in cap["cmd"] and "/tmp/m.json" in cap["cmd"]


def test_invoke_phase_no_mcp_flags_by_default(monkeypatch, tmp_path):
    cap = {}

    def _run(cmd, **kw):
        cap["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(workflow.subprocess, "run", _run)
    workflow.invoke_phase("ship", 2, tmp_path, [])
    assert "--mcp-config" not in cap["cmd"]
