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


# --- ship/gate2 `pass|fail` native fallback (issue #3) ------------------------
# Follow-up to #2: the native fallback for #2 recognised gate1's green|yellow|red
# vocabulary only. The ship phase's gate2 closes with `pass|fail` instead, so a
# ship `claude -p` body that drops the KAGURA_VERDICT= marker but ends in a
# native `## Verdict: pass`/`fail` line still parsed to None → false-negative
# halt. parse_verdict is now phase-aware: for phase="ship" it additionally maps
# native pass→green (proceed) and fail→red (halt). gate1 stays pass|fail-blind.


def test_parse_verdict_ship_native_pass_maps_to_green():
    text = "audit complete...\n\n## Verdict: pass\n"
    assert workflow.parse_verdict(text, phase="ship") == "green"


def test_parse_verdict_ship_native_fail_maps_to_red():
    text = "conformance failure...\n\n## Verdict: fail\n"
    assert workflow.parse_verdict(text, phase="ship") == "red"


def test_parse_verdict_ship_still_reads_green_yellow_red_native():
    # Advisor-only gate2 (the default) closes with green|yellow|red — the ship
    # phase must keep recognising those, not only pass|fail.
    assert workflow.parse_verdict("## Verdict: yellow", phase="ship") == "yellow"


def test_parse_verdict_ship_marker_still_wins_over_native():
    # The KAGURA_VERDICT= marker stays primary even on the ship phase.
    text = "KAGURA_VERDICT=green\nblah\n## Verdict: fail\n"
    assert workflow.parse_verdict(text, phase="ship") == "green"


def test_parse_verdict_ship_native_last_wins_across_vocabularies():
    # A ship transcript can carry advisor green|yellow|red lines followed by the
    # binary gate's pass|fail — the final native verdict line is the decision.
    text = "## Verdict: green\n## Verdict: fail\n"
    assert workflow.parse_verdict(text, phase="ship") == "red"


def test_parse_verdict_gate1_does_not_leak_pass_fail():
    # Acceptance criterion: the pass|fail mapping must NOT leak into gate1. A
    # start-phase body whose only native line is `## Verdict: pass` halts.
    assert workflow.parse_verdict("## Verdict: pass", phase="start") is None


def test_parse_verdict_default_phase_is_pass_fail_blind():
    # Backward-compat: callers that omit phase get the gate1 vocabulary, so the
    # pre-#3 call sites and tests keep their exact behaviour.
    assert workflow.parse_verdict("## Verdict: fail") is None


def test_invoke_phase_ship_native_pass_resolves_to_green(monkeypatch, tmp_path):
    # End-to-end: a ship phase that drops the marker but closes `## Verdict: pass`
    # must resolve to a proceed verdict, not halt.
    def _run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, "gate2 done\n## Verdict: pass\n", "")

    monkeypatch.setattr(workflow.subprocess, "run", _run)
    inv = workflow.invoke_phase("ship", 3, tmp_path, [])
    assert inv.verdict == "green"


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
