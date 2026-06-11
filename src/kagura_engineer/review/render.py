"""Renderers for `ReviewReport` (rich table + JSON). Mirrors run/render.py."""
from __future__ import annotations

import json

from rich.console import Console
from rich.table import Table

from ..profile import to_dict as _profile_dict
from .result import Finding, ReviewLoopReport, ReviewReport, ReviewStatus

_ICON: dict[ReviewStatus, str] = {
    ReviewStatus.OK: "✅",
    ReviewStatus.BLOCKED: "⏸",
    ReviewStatus.FAIL: "❌",
}


def _finding_to_dict(f: Finding) -> dict:
    return {
        "dimension": f.dimension,
        "severity": f.severity,
        "file": f.file,
        "line": f.line,
        "title": f.title,
    }


def _report_to_dict(report: ReviewReport) -> dict:
    return {
        "target": report.target,
        "base": report.base,
        "status": report.status.value,
        "profile": _profile_dict(report.profile) if report.profile else None,
        "verdict": report.verdict,
        "summary": report.summary,
        "findings": [_finding_to_dict(f) for f in report.findings],
        "detail": report.detail,
        "resume_hint": report.resume_hint,
        "report_path": report.report_path,
        "duration_s": round(report.duration_s, 3),
    }


def to_json(report: ReviewReport) -> str:
    return json.dumps(_report_to_dict(report), ensure_ascii=False)


def loop_to_json(report: ReviewLoopReport) -> str:
    return json.dumps(
        {
            "target": report.target,
            "base": report.base,
            "status": report.status.value,
            "profile": _profile_dict(report.profile) if report.profile else None,
            "fixes_attempted": report.fixes_attempted,
            "detail": report.detail,
            "resume_hint": report.resume_hint,
            "duration_s": round(report.duration_s, 3),
            "iterations": [_report_to_dict(r) for r in report.iterations],
        },
        ensure_ascii=False,
    )


def print_table(report: ReviewReport) -> None:
    console = Console()
    title = f"kagura-engineer review {report.target} — {report.status.value} ({report.verdict or '-'})"
    table = Table(title=title)
    table.add_column("severity")
    table.add_column("where")
    table.add_column("dimension")
    table.add_column("title")
    for f in report.findings:
        loc = f"{f.file}:{f.line}" if f.line is not None else f.file
        table.add_row(f.severity, loc, f.dimension, f.title)
    console.print(f"{_ICON[report.status]} {report.detail}")
    if report.findings:
        console.print(table)
    if report.resume_hint:
        console.print(f"resume: {report.resume_hint}")


def print_loop_table(report: ReviewLoopReport) -> None:
    """Render the auto-fix loop: each iteration's verdict, then the final
    review's findings table (the actionable, post-fix state)."""
    console = Console()
    console.print(
        f"{_ICON[report.status]} review --fix {report.target} — "
        f"{report.status.value} · {report.fixes_attempted} fix(es) · {report.detail}"
    )
    if len(report.iterations) > 1:
        trail = " → ".join(f"{r.verdict or r.status.value}" for r in report.iterations)
        console.print(f"iterations: {trail}")
    final = report.final
    if final is not None:
        print_table(final)
    if report.resume_hint:
        console.print(f"resume: {report.resume_hint}")
