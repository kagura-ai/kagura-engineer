"""Enumerate the open issues of a GitHub milestone via `gh`."""
from __future__ import annotations

import subprocess


def list_milestone_issues(milestone: str) -> list[int]:
    """Return the open issue numbers in `milestone`, in `gh`'s order.

    Raises OSError (gh not on PATH) or CalledProcessError (gh error) — the
    orchestrator converts those into a clean FAIL GoalReport.
    """
    proc = subprocess.run(
        ["gh", "issue", "list", "--milestone", milestone, "--state", "open",
         "--json", "number", "--jq", ".[].number"],
        capture_output=True, text=True, check=True,
    )
    return [int(tok) for tok in proc.stdout.split() if tok.strip().isdigit()]
