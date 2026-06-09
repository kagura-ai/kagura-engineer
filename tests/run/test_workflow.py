import subprocess

from kagura_brain.core import BrainResult

from kagura_engineer.mcp import MEMORY_TOOLS
from kagura_engineer.run import workflow
from kagura_engineer.run.workflow import PhaseInvocation


def _fake_brain(stdout="", stderr="", returncode=0, timed_out=False, capture=None):
    """Build a stand-in for ``brain.invoke`` that returns a fixed BrainResult.

    When ``capture`` (a dict) is given, the call's kwargs are recorded into it so
    a test can assert what ``invoke_phase`` forwarded to the launcher seam.
    """
    def _invoke(prompt, **kw):
        if capture is not None:
            capture["prompt"] = prompt
            capture.update(kw)
        return BrainResult(returncode, stdout, stderr, timed_out=timed_out)

    return _invoke


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


def test_parse_verdict_ship_marker_pass_maps_to_green():
    # The primary KAGURA_VERDICT= marker must not be stricter than the native
    # fallback: a ship marker emitted in gate2's own pass|fail vocabulary
    # (despite the green|yellow|red hint) is normalised, not false-halted.
    assert workflow.parse_verdict("KAGURA_VERDICT=pass\n", phase="ship") == "green"


def test_parse_verdict_ship_marker_fail_maps_to_red():
    assert workflow.parse_verdict("KAGURA_VERDICT=fail\n", phase="ship") == "red"


def test_parse_verdict_ship_marker_green_unchanged():
    # green|yellow|red markers pass through the ship normalisation untouched.
    assert workflow.parse_verdict("KAGURA_VERDICT=yellow\n", phase="ship") == "yellow"


def test_parse_verdict_gate1_marker_does_not_map_pass():
    # The marker normalisation is phase-gated too: a start-phase `pass` marker
    # stays the raw (gate-halting) token, never green.
    assert workflow.parse_verdict("KAGURA_VERDICT=pass\n", phase="start") == "pass"


def test_invoke_phase_ship_marker_fail_resolves_to_red(monkeypatch, tmp_path):
    monkeypatch.setattr(
        workflow.brain, "invoke",
        _fake_brain(stdout="gate2 blocked\nKAGURA_VERDICT=fail\n"),
    )
    assert workflow.invoke_phase("ship", 3, tmp_path, []).verdict == "red"


def test_invoke_phase_ship_native_pass_resolves_to_green(monkeypatch, tmp_path):
    # End-to-end: a ship phase that drops the marker but closes `## Verdict: pass`
    # must resolve to a proceed verdict, not halt.
    monkeypatch.setattr(
        workflow.brain, "invoke",
        _fake_brain(stdout="gate2 done\n## Verdict: pass\n"),
    )
    inv = workflow.invoke_phase("ship", 3, tmp_path, [])
    assert inv.verdict == "green"


def test_parse_pr_url_reads_marker():
    assert workflow.parse_pr_url("KAGURA_PR_URL=https://github.com/o/r/pull/5\n") == "https://github.com/o/r/pull/5"


def test_parse_pr_url_none_when_absent_or_dash():
    assert workflow.parse_pr_url("KAGURA_PR_URL=-\n") is None
    assert workflow.parse_pr_url("nothing") is None


def test_invoke_phase_routes_through_harness_brain_and_parses(monkeypatch, tmp_path):
    # invoke_phase no longer constructs a `claude -p` argv itself — it delegates
    # to the harness brain.invoke seam (which owns the #34 key-strip) and maps
    # the BrainResult back onto a PhaseInvocation.
    cap = {}
    monkeypatch.setattr(
        workflow.brain, "invoke",
        _fake_brain(
            stdout="work...\nKAGURA_VERDICT=green\nKAGURA_PR_URL=https://x/pull/1\n",
            capture=cap,
        ),
    )
    inv = workflow.invoke_phase("ship", 3, tmp_path, ["g"])
    assert isinstance(inv, PhaseInvocation)
    assert inv.verdict == "green"
    assert inv.pr_url == "https://x/pull/1"
    assert inv.returncode == 0
    assert inv.phase == "ship"
    # Forwarded to the launcher seam: worktree cwd + the pre-approved memory tools.
    assert cap["cwd"] == tmp_path
    assert tuple(cap["allowed_tools"]) == MEMORY_TOOLS


def test_invoke_phase_nonzero_returncode_keeps_output(monkeypatch, tmp_path):
    monkeypatch.setattr(
        workflow.brain, "invoke", _fake_brain(returncode=1, stderr="boom"),
    )
    inv = workflow.invoke_phase("start", 3, tmp_path, [])
    assert inv.returncode == 1
    assert inv.verdict is None
    assert "boom" in inv.stderr


def test_invoke_phase_timeout_returns_marker(monkeypatch, tmp_path):
    # No output at all: the timeout label falls back to BrainResult.detail().
    monkeypatch.setattr(
        workflow.brain, "invoke",
        _fake_brain(returncode=-1, timed_out=True),
    )
    inv = workflow.invoke_phase("start", 3, tmp_path, [])
    assert inv.returncode == -1
    assert inv.timed_out is True
    assert inv.stderr == "timed out"


def test_invoke_phase_timeout_preserves_partial_output(monkeypatch, tmp_path):
    # detail() surfaces real stderr over the generic label; partial stdout is kept.
    monkeypatch.setattr(
        workflow.brain, "invoke",
        _fake_brain(returncode=-1, stdout="partial work\n", stderr="warn", timed_out=True),
    )
    inv = workflow.invoke_phase("start", 3, tmp_path, [])
    assert inv.timed_out is True
    assert inv.stdout == "partial work\n"
    assert inv.stderr == "warn"


def test_build_prompt_unattended_adds_instruction():
    p = workflow.build_prompt("start", 1, [], unattended=True)
    assert "UNATTENDED" in p


def test_build_prompt_default_has_no_unattended():
    assert "UNATTENDED" not in workflow.build_prompt("start", 1, [])


def test_invoke_phase_forwards_unattended_into_prompt(monkeypatch, tmp_path):
    cap = {}
    monkeypatch.setattr(
        workflow.brain, "invoke",
        _fake_brain(stdout="KAGURA_VERDICT=green\n", capture=cap),
    )
    workflow.invoke_phase("ship", 2, tmp_path, [], unattended=True)
    assert "UNATTENDED" in cap["prompt"]


def test_build_prompt_mcp_note_when_enabled():
    p = workflow.build_prompt("start", 1, [], mcp_enabled=True)
    assert "mcp__kagura-memory__recall" in p
    assert "UNTRUSTED" in p


def test_build_prompt_no_mcp_note_by_default():
    assert "mcp__kagura-memory" not in workflow.build_prompt("start", 1, [])


def test_invoke_phase_forwards_mcp_config_to_brain(monkeypatch, tmp_path):
    cap = {}
    monkeypatch.setattr(
        workflow.brain, "invoke",
        _fake_brain(stdout="KAGURA_VERDICT=green\n", capture=cap),
    )
    workflow.invoke_phase("ship", 2, tmp_path, [], mcp_config="/tmp/m.json")
    assert cap["mcp_config"] == "/tmp/m.json"


def test_invoke_phase_no_mcp_config_by_default(monkeypatch, tmp_path):
    cap = {}
    monkeypatch.setattr(workflow.brain, "invoke", _fake_brain(capture=cap))
    workflow.invoke_phase("ship", 2, tmp_path, [])
    assert cap["mcp_config"] is None


# --- issue #9: the implement phase ----------------------------------------
# There is no `/gh-issue-driven:implement` skill — the implement phase drives
# implementation directly (TDD discipline + scope-based orchestration), so its
# prompt must NOT invoke a non-existent slash command.


def test_build_prompt_implement_drives_tdd_not_a_slash_command():
    p = workflow.build_prompt("implement", 9, ["guardrail: TDD"])
    assert "/gh-issue-driven:implement" not in p  # no such skill exists
    assert "9" in p and "guardrail: TDD" in p     # issue + grounding present
    low = p.lower()
    assert "test" in low and "commit" in low       # test-first + must commit
    assert "KAGURA_VERDICT=" in p                   # still emits the marker


def test_build_prompt_implement_forwards_unattended_and_mcp():
    p = workflow.build_prompt("implement", 1, [], unattended=True, mcp_enabled=True)
    assert "UNATTENDED" in p
    assert "mcp__kagura-memory__recall" in p


def test_build_prompt_start_still_invokes_slash_command():
    assert "/gh-issue-driven:start 9" in workflow.build_prompt("start", 9, [])


def test_build_prompt_ship_still_invokes_slash_command():
    assert "/gh-issue-driven:ship 2" in workflow.build_prompt("ship", 2, [])


def test_head_rev_none_for_non_git_dir(tmp_path):
    # Best-effort: a non-git path returns None rather than raising, so the
    # implement empty-commit check degrades to "skip" instead of crashing.
    assert workflow.head_rev(tmp_path) is None


def test_head_rev_returns_sha_for_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t",
         "commit", "--allow-empty", "-qm", "x"],
        cwd=tmp_path, check=True,
    )
    sha = workflow.head_rev(tmp_path)
    assert sha and len(sha) >= 7


# --- persist child stdout on a (ship) FAIL for diagnosis (issue #38) ----------


def test_persist_phase_stdout_writes_captured_output(tmp_path):
    # issue #38: the child `claude -p` reasoning is the only trace of *why* ship
    # skipped push / PR, and `run --json` suppresses it. Persist it under the
    # worktree's gitignored `.kagura/` dir so the skip is diagnosable.
    inv = PhaseInvocation("ship", 0, "I decided to skip the PR because…", "", "green", None)
    path = workflow.persist_phase_stdout(tmp_path, inv)
    assert path is not None
    assert path == tmp_path / ".kagura" / "ship-stdout.log"
    assert path.exists()
    assert "skip the PR" in path.read_text()


def test_persist_phase_stdout_includes_stderr_when_present(tmp_path):
    inv = PhaseInvocation("ship", 1, "out", "boom on stderr", None, None)
    path = workflow.persist_phase_stdout(tmp_path, inv)
    body = path.read_text()
    assert "out" in body and "boom on stderr" in body


def test_persist_phase_stdout_returns_none_on_unwritable_path(tmp_path):
    # Best-effort: a filesystem error must return None (the FAIL is already
    # recorded; a missing diagnostic log must never mask it or crash the run).
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir")  # .kagura/ parent can't be created under a file
    inv = PhaseInvocation("ship", 0, "x", "", "green", None)
    assert workflow.persist_phase_stdout(blocker, inv) is None
