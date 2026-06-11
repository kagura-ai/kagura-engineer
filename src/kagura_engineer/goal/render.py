"""Renderers for `GoalReport` (rich table + JSON). Mirrors run/render.py."""
from __future__ import annotations

import json

from rich.console import Console
from rich.table import Table

from ..profile import to_dict as _profile_dict
from ..run.result import STATUS_ICON as _ICON
from .result import GoalReport


def to_json(report: GoalReport) -> str:
    return json.dumps(
        {
            "milestone": report.milestone,
            "status": report.status.value,
            "profile": _profile_dict(report.profile) if report.profile else None,
            "completed": report.completed,
            "total": len(report.issues),
            "detail": report.detail,
            "resume_hint": report.resume_hint,
            "duration_s": round(report.duration_s, 3),
            "issues": [
                {"issue": r.issue, "status": r.status.value, "pr_url": r.pr_url}
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
        table.add_column("PR")
        for r in report.issues:
            table.add_row(_ICON[r.status], f"#{r.issue}", r.status.value, r.pr_url or "")
        console.print(table)
    if report.resume_hint:
        console.print(f"resume: {report.resume_hint}")
