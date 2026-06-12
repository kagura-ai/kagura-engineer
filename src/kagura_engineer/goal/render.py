"""Renderers for `GoalReport` (rich table + JSON). Mirrors run/render.py."""
from __future__ import annotations

import json

from rich.console import Console
from rich.table import Table

from ..profile import review_to_dict_or_none as _review_dict
from ..profile import to_dict_or_none as _profile_dict
from ..run.result import STATUS_ICON as _ICON
from ..run.result import RunReport
from .result import GoalReport


def _review_cell(r: RunReport) -> str:
    """issue #74: a compact per-issue reviewer cell for the milestone table —
    `provider @ model`, or `—` when no code review ran for that issue."""
    rev = r.review
    if rev is None:
        return "—"
    return f"{rev.provider} @ {rev.model or 'default'}"


def to_json(report: GoalReport) -> str:
    return json.dumps(
        {
            "milestone": report.milestone,
            "status": report.status.value,
            "profile": _profile_dict(report.profile),
            "completed": report.completed,
            "total": len(report.issues),
            "detail": report.detail,
            "resume_hint": report.resume_hint,
            "duration_s": round(report.duration_s, 3),
            "issues": [
                {
                    "issue": r.issue,
                    "status": r.status.value,
                    "pr_url": r.pr_url,
                    # issue #74: each issue records the reviewer its run used
                    # (null for an issue that halted before code review).
                    "review": _review_dict(r.review),
                }
                for r in report.issues
            ],
        },
        ensure_ascii=False,
    )


def print_table(report: GoalReport) -> None:
    console = Console()
    console.print(
        f"{_ICON[report.status]} goal {report.milestone} — {report.status.value} · "
        f"{report.completed}/{len(report.issues)} shipped · {report.detail}"
    )
    if report.issues:
        table = Table()
        table.add_column("")
        table.add_column("issue")
        table.add_column("status")
        table.add_column("review")
        table.add_column("PR")
        for r in report.issues:
            table.add_row(_ICON[r.status], f"#{r.issue}", r.status.value,
                          _review_cell(r), r.pr_url or "")
        console.print(table)
    if report.resume_hint:
        console.print(f"resume: {report.resume_hint}")
