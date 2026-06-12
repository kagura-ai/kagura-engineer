"""Enumerate the open issues of a GitHub milestone via `gh`."""
from __future__ import annotations

from .._launch import run_text


def list_milestone_issues(milestone: str) -> list[int]:
    """Return the open issue numbers in `milestone`, in `gh`'s order.

    Raises OSError (gh not on PATH) or CalledProcessError (gh error) — the
    orchestrator converts those into a clean FAIL GoalReport.
    """
    # run_text: launch a Windows `gh.cmd` shim correctly and decode UTF-8/replace
    # so a cp932 console can't crash the reader thread (issue #78).
    proc = run_text(
        ["gh", "issue", "list", "--milestone", milestone, "--state", "open",
         "--json", "number", "--jq", ".[].number"],
        capture_output=True, check=True,
    )
    return [int(tok) for tok in proc.stdout.split() if tok.strip().isdigit()]
