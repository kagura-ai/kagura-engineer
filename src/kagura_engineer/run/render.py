"""Renderers for `RunReport` (rich table + JSON). Mirrors setup/render.py."""
from __future__ import annotations

import json

from rich.console import Console
from rich.table import Table

from ..profile import to_dict_or_none as _profile_dict
from .result import STATUS_ICON as _ICON, PhaseResult, RunReport


def _phase_to_dict(p: PhaseResult) -> dict:
    return {
        "name": p.name,
        "status": p.status.value,
        "detail": p.detail,
        "verdict": p.verdict,
        "duration_s": round(p.duration_s, 3),
    }


def to_json(report: RunReport) -> str:
    return json.dumps(
        {
            "issue": report.issue,
            "status": report.status.value,
            "profile": _profile_dict(report.profile),
            "pr_url": report.pr_url,
            "worktree": report.worktree,
            "resume_hint": report.resume_hint,
            "phases": [_phase_to_dict(p) for p in report.phases],
            "duration_s": round(report.duration_s, 3),
        },
        ensure_ascii=False,
    )


def print_table(report: RunReport) -> None:
    table = Table(title=f"kagura-engineer run #{report.issue} — {report.status.value}")
    table.add_column("")
    table.add_column("phase")
    table.add_column("status")
    table.add_column("verdict")
    table.add_column("detail")
    for p in report.phases:
        table.add_row(_ICON[p.status], p.name, p.status.value, p.verdict or "", p.detail)
    console = Console()
    console.print(table)
    if report.pr_url:
        console.print(f"PR: {report.pr_url}")
    if report.resume_hint:
        console.print(f"resume: {report.resume_hint}")
