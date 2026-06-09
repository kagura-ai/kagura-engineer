"""Guards for the #40 single-launcher migration onto kagura-brain.

After #40 the headless `claude -p` launcher is owned solely by
`kagura_brain.claude.invoke`. No kagura-engineer source may construct a
`claude -p` argv of its own — that is the unhardened twin (#34) the migration
removed. These tests are the regression backstop for that contract.
"""
from __future__ import annotations

import re
from pathlib import Path

from kagura_engineer.mcp import MEMORY_TOOLS

_SRC = Path(__file__).resolve().parent.parent / "src" / "kagura_engineer"
# A `claude -p` argv literal: `["claude", "-p", ...]` (tolerant of whitespace).
_CLAUDE_P_ARGV = re.compile(r"""\[\s*["']claude["']\s*,\s*["']-p["']""")


def test_memory_tools_are_the_recall_remember_vocabulary():
    assert MEMORY_TOOLS == (
        "mcp__kagura-memory__recall",
        "mcp__kagura-memory__remember",
    )


def test_no_claude_p_argv_constructed_in_source():
    offenders = [
        py.relative_to(_SRC).as_posix()
        for py in _SRC.rglob("*.py")
        if _CLAUDE_P_ARGV.search(py.read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        "claude -p argv must be built only by kagura-brain, "
        f"but these modules construct one: {offenders}"
    )
