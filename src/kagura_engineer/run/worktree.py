"""Per-run git worktree isolation.

Each `run <issue#>` gets its own worktree named `run-<issue#>`, placed
OUTSIDE the repo working tree (in a sibling `.kagura-runs/<repo-name>/`
dir) so it never pollutes the repo's `git status`. The name is
deterministic so a resumed run finds the same worktree.

Product code uses plain `subprocess.run(["git", ...])` — real git, no
RTK proxy in the path (RTK only rewrites the agent's Bash-tool git).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .._launch import run_text

_TIMEOUT_S = 30


class WorktreeError(RuntimeError):
    """A `git worktree` command failed."""


def worktree_root(repo_root: Path) -> Path:
    """Sibling dir that holds this repo's run worktrees."""
    return repo_root.parent / ".kagura-runs" / repo_root.name


def worktree_path(repo_root: Path, issue: int, *, label: str | None = None) -> Path:
    """Full path to the worktree for `issue` under this repo's run root.

    `label` names an isolated *arm* of the same issue (e.g. the eval harness's
    ``grounded``/``control`` arms — issue #57): ``run-<issue>-<label>`` instead of
    ``run-<issue>``, so two arms of one issue never share a worktree (and so the
    grounded arm's commits cannot contaminate the control arm). ``None`` keeps the
    historical ``run-<issue>`` name — normal runs are unchanged.
    """
    name = f"run-{issue}" if label is None else f"run-{issue}-{label}"
    return worktree_root(repo_root) / name


def ensure_worktree(
    repo_root: Path, issue: int, *, base: str = "HEAD", label: str | None = None
) -> Path:
    """Return the worktree path, creating it off `base` if absent.

    If the path already exists this is a resume: return it untouched. `label`
    selects an isolated per-arm worktree (see `worktree_path`).
    """
    path = worktree_path(repo_root, issue, label=label)
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # run_text: utf-8/replace so a non-ASCII path or localized git message
        # can't crash the reader thread on a cp932 console (issue #78).
        proc = run_text(
            ["git", "worktree", "add", str(path), base],
            cwd=repo_root, capture_output=True, timeout=_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise WorktreeError(f"git worktree add failed: {exc}") from exc
    if proc.returncode != 0:
        raise WorktreeError(f"git worktree add failed: {proc.stderr.strip()}")
    return path


def remove_worktree(path: Path, *, repo_root: Path | None = None) -> None:
    """Best-effort cleanup of a run worktree.

    Errors are intentionally suppressed — a failed removal must not abort
    the caller. Pass `repo_root` as cwd so the command works even if the
    process CWD is inside the worktree being removed (git refuses that).
    """
    run_text(
        ["git", "worktree", "remove", "--force", str(path)],
        cwd=repo_root, capture_output=True, timeout=_TIMEOUT_S,
    )
