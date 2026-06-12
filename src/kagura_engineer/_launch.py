"""Cross-platform subprocess launch helpers (issue #78).

Two native-Windows traps motivate this module:

1. **`.cmd`/`.bat` shim resolution.** Windows `CreateProcess` only auto-appends
   `.exe`, not `.cmd`/`.bat`, so `subprocess.run(["claude", ...])` raises
   `[WinError 2]` for an npm/scoop `.cmd` shim even though `shutil.which("claude")`
   resolves `claude.cmd`. `git`/`gh` (real `.exe`) work; `claude`/`codex` (npm
   shims) do not. `launch_argv` resolves argv[0] and routes a `.cmd`/`.bat`
   through `%COMSPEC%` /c while keeping `shell=False`. Mirrors kagura_brain's
   `_launch_argv` (issue #17 there).

2. **cp932 decode crashes.** `subprocess.run(text=True)` decodes the child's
   stdout with the console codec (cp932 on a JP Windows box); a child printing a
   non-cp932 byte raises `UnicodeDecodeError` in the reader thread and crashes
   the run. `run_text` forces UTF-8 with `errors="replace"` so capture degrades
   to a replacement char instead of dying.

Both fixes are no-ops on POSIX: `launch_argv` returns the argv unchanged off
Windows, and UTF-8 is already the default decode there — so behaviour on
Linux/macOS (and CI) is byte-for-byte identical.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys


def launch_argv(cmd: list[str]) -> list[str]:
    """Return an argv that is launchable on native Windows.

    On Windows, resolve argv[0] via ``shutil.which`` and wrap a ``.cmd``/``.bat``
    shim in ``%COMSPEC% /c`` (default ``cmd.exe``), keeping ``shell=False``. A
    real executable, an unresolvable name, or any non-Windows platform returns
    ``cmd`` unchanged.
    """
    if sys.platform != "win32" or not cmd:
        return cmd
    resolved = shutil.which(cmd[0])
    if resolved is None:
        return cmd  # let subprocess raise a clear FileNotFoundError
    if resolved.lower().endswith((".cmd", ".bat")):
        comspec = os.environ.get("COMSPEC") or "cmd.exe"
        return [comspec, "/c", resolved, *cmd[1:]]
    return cmd


def run_text(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """``subprocess.run`` that is Windows-launchable and cp932-safe (issue #78).

    Applies :func:`launch_argv` so a Windows ``.cmd`` shim resolves, and forces
    UTF-8 decoding with ``errors="replace"`` so a child emitting non-cp932 bytes
    cannot crash the capture reader thread. Callers pass the usual
    ``capture_output`` / ``timeout`` / ``check`` kwargs; ``encoding`` and
    ``errors`` default here but a caller may override them.
    """
    kwargs.setdefault("encoding", "utf-8")
    kwargs.setdefault("errors", "replace")
    return subprocess.run(launch_argv(cmd), **kwargs)
