from __future__ import annotations

import json

from rich.console import Console
from rich.table import Table

from .registry import overall_status
from .result import CheckResult

_ICON = {"ok": "✅", "warn": "⚠️", "fail": "❌"}


def to_json(results: list[CheckResult]) -> str:
    return json.dumps(
        {
            "overall": overall_status(results).value,
            "checks": [
                {
                    "name": r.name,
                    "status": r.status.value,
                    "detail": r.detail,
                    "fix_hint": r.fix_hint,
                }
                for r in results
            ],
        }
    )


def print_table(results: list[CheckResult]) -> None:
    table = Table(title="kagura-engineer doctor")
    table.add_column("")
    table.add_column("check")
    table.add_column("detail")
    table.add_column("fix")
    for r in results:
        table.add_row(_ICON[r.status.value], r.name, r.detail, r.fix_hint or "")
    Console().print(table)
