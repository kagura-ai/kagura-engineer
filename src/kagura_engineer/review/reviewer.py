"""Launch the kagura-code-reviewer console script and collect its envelope.

We invoke the reviewer as a separate process (it is a separate product;
`run` never calls it). The envelope is read from the `--out` file when the
reviewer wrote one, falling back to stdout. The no-changes case is special:
the reviewer prints `No changes to review.` and exits 0 *before* writing
`--out`, so we detect that line and report `no_changes=True`.

OSError (reviewer not on PATH) is NOT caught here — the orchestrator's guard
turns it into a clean FAIL ReviewReport; mirrors run/workflow.py.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from kagura_claude_harness.proc import as_text

from .envelope import ReviewEnvelope

_REVIEW_TIMEOUT_S = 1800  # 30 min — a large diff with high effort can be slow
_NO_CHANGES = "No changes to review."


@dataclass(frozen=True)
class ReviewerResult:
    returncode: int
    stdout: str
    stderr: str
    envelope: ReviewEnvelope
    no_changes: bool = False
    timed_out: bool = False


def build_argv(
    *, base: str, head: str, repo: Path, out: Path,
    context_file: Path | None, model: str | None, effort: str,
) -> list[str]:
    argv = [
        "kagura-code-reviewer",
        "--base", base,
        "--head", head,
        "--repo", str(repo),
        "--format", "json",
        "--out", str(out),
        "--effort", effort,
    ]
    if context_file is not None:
        argv += ["--context-file", str(context_file)]
    if model:
        argv += ["--model", model]
    return argv


def resolve_head(target: str) -> str:
    """A bare integer is treated as a PR number and resolved to its head branch
    via `gh`; anything else is returned verbatim as a git ref. On any gh error
    the raw token is returned so the reviewer's own git diff fails loudly
    rather than us guessing a ref."""
    if not target.isdigit():
        return target
    try:
        proc = subprocess.run(
            ["gh", "pr", "view", target, "--json", "headRefName", "-q", ".headRefName"],
            capture_output=True, text=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return target
    branch = proc.stdout.strip()
    return branch or target


def run_reviewer(
    *, base: str, head: str, repo: Path, out: Path,
    context_file: Path | None = None, model: str | None = None,
    effort: str = "med", timeout: int = _REVIEW_TIMEOUT_S,
) -> ReviewerResult:
    argv = build_argv(
        base=base, head=head, repo=repo, out=out,
        context_file=context_file, model=model, effort=effort,
    )
    # Clear any stale report from a prior run so a reviewer that exits 0
    # without rewriting --out can never be mis-gated on old findings.
    out.unlink(missing_ok=True)
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return ReviewerResult(
            -1, as_text(exc.stdout), as_text(exc.stderr) or "timed out",
            ReviewEnvelope(parsed=False), timed_out=True,
        )

    no_changes = _NO_CHANGES in (proc.stdout or "")
    if out.is_file() and (content := out.read_text()).strip():
        env = ReviewEnvelope.from_text(content)
    else:
        env = ReviewEnvelope.from_text(proc.stdout)
    return ReviewerResult(proc.returncode, proc.stdout, proc.stderr, env, no_changes=no_changes)
