"""Renderers for `SetupReport` (rich table + JSON).

Mirrors `doctor/render.py`: same `to_json` / `print_table` split, same
icon mapping. The bucket ordering (ran / skipped / needs_user / failed)
is canonical — it is the order in which a human operator triages a
provisioning run, and it is also the order in which a CI script greps
the JSON to decide whether to fail the job.
"""
from __future__ import annotations

import json

from rich.console import Console
from rich.table import Table

from .result import SetupReport, StepResult, StepStatus

_ICON: dict[StepStatus, str] = {
    StepStatus.OK: "✅",
    StepStatus.SKIPPED: "⏭",
    StepStatus.NEEDS_USER: "🙋",
    StepStatus.FAIL: "❌",
}


def _result_to_dict(r: StepResult) -> dict:
    return {
        "name": r.name,
        "status": r.status.value,
        "detail": r.detail,
        "fix_hint": r.fix_hint,
        "duration_s": round(r.duration_s, 3),
    }


def to_json(report: SetupReport) -> str:
    return json.dumps(
        {
            "ran": [_result_to_dict(r) for r in report.ran],
            "skipped": [_result_to_dict(r) for r in report.skipped],
            "needs_user": [_result_to_dict(r) for r in report.needs_user],
            "failed": [_result_to_dict(r) for r in report.failed],
            "duration_s": round(report.duration_s, 3),
            "is_blocked": report.is_blocked,
        },
        ensure_ascii=False,
    )


def print_table(report: SetupReport) -> None:
    table = Table(title="kagura-engineer setup")
    table.add_column("")
    table.add_column("step")
    table.add_column("status")
    table.add_column("detail")
    table.add_column("fix")
    # Canonical bucket order: successes first (so the user sees what worked),
    # then attention items (skipped is informational, not a problem).
    for r in [*report.ran, *report.skipped, *report.needs_user, *report.failed]:
        table.add_row(
            _ICON[r.status],
            r.name,
            r.status.value,
            r.detail,
            r.fix_hint or "",
        )
    Console().print(table)
