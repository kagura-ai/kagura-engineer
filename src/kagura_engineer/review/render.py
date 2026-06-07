"""Renderers for `ReviewReport` (rich table + JSON). Mirrors run/render.py."""
from __future__ import annotations

import json

from rich.console import Console
from rich.table import Table

from .result import Finding, ReviewReport, ReviewStatus

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


def to_json(report: ReviewReport) -> str:
    return json.dumps(
        {
            "target": report.target,
            "base": report.base,
            "status": report.status.value,
            "verdict": report.verdict,
            "summary": report.summary,
            "findings": [_finding_to_dict(f) for f in report.findings],
            "detail": report.detail,
            "resume_hint": report.resume_hint,
            "report_path": report.report_path,
            "duration_s": round(report.duration_s, 3),
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
