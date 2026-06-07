"""Drive one gh-issue-driven phase via a headless `claude -p` call.

We do NOT depend on gh-issue-driven's internal output format. Instead the
prompt instructs the session to print two machine-readable marker lines
at the very end:

    KAGURA_VERDICT=<green|yellow|red>
    KAGURA_PR_URL=<url|->

`invoke_phase` runs `claude -p <prompt>` with the worktree as cwd, then
parses those markers. A missing verdict marker parses to None, which the
gate treats as a halt (safe default).

Phases are separate `claude -p` calls because gh-issue-driven checkpoints
to the branch + memory between phases, so each call resumes cleanly.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

_PHASE_TIMEOUT_S = 1800  # 30 min per phase

_VERDICT_RE = re.compile(r"^KAGURA_VERDICT=(\w+)\s*$", re.MULTILINE)
_PR_RE = re.compile(r"^KAGURA_PR_URL=(\S+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class PhaseInvocation:
    phase: str
    returncode: int
    stdout: str
    stderr: str
    verdict: str | None
    pr_url: str | None
    timed_out: bool = False


def build_prompt(phase: str, issue: int, grounding: list[str]) -> str:
    context = "\n".join(f"- {g}" for g in grounding) or "- (no prior memory)"
    return (
        "You are running inside an automated kagura-engineer run.\n"
        "Relevant memory (recall + pinned guardrails):\n"
        f"{context}\n\n"
        f"Run the slash command `/gh-issue-driven:{phase} {issue}` to completion.\n"
        "When finished, print these two lines LAST, exactly:\n"
        "KAGURA_VERDICT=<green|yellow|red>   (the phase gate verdict)\n"
        "KAGURA_PR_URL=<pull-request-url or - if none>\n"
    )


def parse_verdict(text: str) -> str | None:
    matches = _VERDICT_RE.findall(text or "")
    return matches[-1].lower() if matches else None


def parse_pr_url(text: str) -> str | None:
    matches = _PR_RE.findall(text or "")
    if not matches:
        return None
    url = matches[-1]
    return None if url == "-" else url


def invoke_phase(
    phase: str, issue: int, worktree: Path, grounding: list[str],
    *, timeout: int = _PHASE_TIMEOUT_S,
) -> PhaseInvocation:
    prompt = build_prompt(phase, issue, grounding)
    # OSError (claude not on PATH) is deliberately NOT caught here: the
    # run guard (doctor's blocking gh-issue-driven/claude check) verifies
    # claude is launchable before invoke_phase is ever reached.
    try:
        proc = subprocess.run(
            ["claude", "-p", prompt],
            cwd=worktree, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        # Preserve any partial output captured before the kill — invaluable
        # for diagnosing what a 30-min phase was doing when it stalled.
        return PhaseInvocation(
            phase, -1, exc.stdout or "", exc.stderr or "timed out",
            None, None, timed_out=True,
        )
    return PhaseInvocation(
        phase, proc.returncode, proc.stdout, proc.stderr,
        parse_verdict(proc.stdout), parse_pr_url(proc.stdout),
    )
