"""Launch a headless `claude -p` to fix blocking review findings.

Plan 4b: in the auto-fix loop, when the reviewer returns a red verdict, the
ACTOR (claude) — not the bounded reviewer — attempts the fix. This mirrors
`run/workflow.py::invoke_phase`: a single `claude -p` subprocess in the repo,
with the same TimeoutExpired-bytes guard (`text=True` still yields bytes on a
timeout that captured partial output).

The fixer is pointed at the persisted raw report (`.kagura/review.json`) so it
can read the full findings (rationale/suggestion) the display-slice Finding
drops. It is instructed to fix ONLY blocking findings and to COMMIT (so the
re-review's `git diff base..HEAD` sees the change) but NOT push.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .result import Finding

_FIX_TIMEOUT_S = 1800  # 30 min — match run's phase timeout


def _as_text(value: bytes | str | None) -> str:
    """TimeoutExpired carries raw bytes even under text=True; normalize to str."""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value or ""


@dataclass(frozen=True)
class FixerResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


def build_fix_prompt(report_path: str | None, findings: list[Finding]) -> str:
    lines = []
    for f in findings:
        loc = f"{f.file}:{f.line}" if f.line is not None else f.file
        lines.append(f"- [{f.severity}] {loc} — {f.title}")
    listed = "\n".join(lines) or "- (see the report)"
    src = (
        f"The full machine-readable report (with rationale and suggestions) is at:\n{report_path}\n"
        if report_path
        else "No report file is available; use the finding list below.\n"
    )
    return (
        "You are fixing code-review findings inside an automated "
        "kagura-engineer `review --fix` loop.\n\n"
        f"{src}\n"
        "Fix ONLY the blocking findings below (severity HIGH/CRITICAL). Make "
        "minimal, correct changes — do not refactor unrelated code.\n\n"
        f"Blocking findings:\n{listed}\n\n"
        "When done: run the project's tests if present, then COMMIT your changes "
        "with a clear message (e.g. 'fix: address review findings'). Do NOT push."
    )


def run_fixer(
    repo: Path, prompt: str, *, timeout: int = _FIX_TIMEOUT_S
) -> FixerResult:
    # OSError (claude not on PATH) is NOT caught here — the loop's guard
    # (doctor's blocking claude check) verifies claude is launchable first,
    # and the loop converts any leak to a clean FAIL. Mirrors run/workflow.py.
    try:
        proc = subprocess.run(
            ["claude", "-p", prompt],
            cwd=repo, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return FixerResult(-1, _as_text(exc.stdout), _as_text(exc.stderr) or "timed out", timed_out=True)
    return FixerResult(proc.returncode, proc.stdout, proc.stderr)
