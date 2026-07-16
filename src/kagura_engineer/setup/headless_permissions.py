"""Provision Claude Code permissions needed by unattended harness runs.

The repository allowlist is safe to scaffold mechanically, but widening it
requires an explicit human confirmation. Workspace trust is intentionally
different: Claude Code owns it in ``~/.claude.json`` and this module never
reads or writes that file. Instead, the user opens Claude once in each run
location and attests that both trust dialogs were accepted.
"""
from __future__ import annotations

import json
import os
import stat
import tempfile
import time
from pathlib import Path
from typing import Callable

from ..config import Config
from .result import StepResult, StepStatus

BASELINE_PERMISSIONS: tuple[str, ...] = (
    "Edit",
    "Write",
    "NotebookEdit",
    "Bash(git status *)",
    "Bash(git diff *)",
    "Bash(git log *)",
    "Bash(git add *)",
    "Bash(git commit *)",
    "Bash(git checkout *)",
    "Bash(git push *)",
    "Bash(git fetch *)",
    "Bash(gh auth status)",
    "Bash(gh issue view *)",
    "Bash(gh pr create *)",
    "Bash(gh pr view *)",
    "Bash(gh api *)",
    "Bash(pytest *)",
    "Bash(uv run *)",
)


def _load_settings(target: Path) -> tuple[dict, list] | str:
    parent_is_junction = bool(getattr(target.parent, "is_junction", lambda: False)())
    target_is_junction = bool(getattr(target, "is_junction", lambda: False)())
    unsafe_link = (
        target.parent.is_symlink()
        or parent_is_junction
        or target.is_symlink()
        or target_is_junction
    )
    if unsafe_link:
        return (
            f"refusing to widen permissions through a symlink or junction: {target}"
        )
    if not target.exists():
        return {}, []
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return f"could not read valid JSON from {target}: {type(exc).__name__}: {exc}"
    if not isinstance(value, dict):
        return f"{target} must contain a JSON object"
    if "permissions" not in value:
        return value, []
    permissions = value["permissions"]
    if not isinstance(permissions, dict):
        return f"{target}: permissions must be a JSON object"
    if "allow" not in permissions:
        return value, []
    allow = permissions["allow"]
    if not isinstance(allow, list):
        return f"{target}: permissions.allow must be a JSON array"
    return value, allow


def _write_settings_atomic(target: Path, settings: dict) -> None:
    """Atomically replace settings while retaining an existing file's mode."""
    target.parent.mkdir(parents=True, exist_ok=True)
    old_mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else None
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            json.dump(settings, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            temp_name = stream.name
        if old_mode is not None:
            os.chmod(temp_name, old_mode)
        os.replace(temp_name, target)
        temp_name = None
    finally:
        if temp_name is not None:
            Path(temp_name).unlink(missing_ok=True)


def _confirmed(input_fn: Callable[[str], str], prompt: str) -> bool:
    try:
        return input_fn(prompt).strip().lower() in {"y", "yes"}
    except (EOFError, KeyboardInterrupt):
        return False


def ensure_headless_permissions(
    cfg: Config,
    *,
    no_input: bool,
    dry_run: bool,
    repo_dir: Path | None = None,
    input_fn: Callable[[str], str] = input,
) -> StepResult:
    """Merge the baseline and require human workspace-trust attestation."""
    name = "headless-permissions"
    started = time.monotonic()
    repo_dir = (repo_dir if repo_dir is not None else Path.cwd()).resolve()
    target = repo_dir / ".claude" / "settings.json"
    worktree_parent = repo_dir.parent / ".kagura-runs" / repo_dir.name

    def result(status: StepStatus, detail: str, fix_hint: str | None = None) -> StepResult:
        return StepResult(
            name,
            status,
            detail,
            fix_hint=fix_hint,
            duration_s=time.monotonic() - started,
        )

    if cfg.brain_backend == "codex":
        return result(
            StepStatus.SKIPPED,
            "brain_backend=codex: Claude Code permissions and trust do not apply",
        )

    loaded = _load_settings(target)
    if isinstance(loaded, str):
        return result(
            StepStatus.FAIL,
            loaded,
            "repair .claude/settings.json manually, then re-run "
            "`kagura-engineer setup --fix headless-permissions`",
        )
    settings, allow = loaded
    missing = [
        permission for permission in BASELINE_PERMISSIONS if permission not in allow
    ]
    trust_hint = (
        f"create {worktree_parent} if needed; open `claude` once with cwd={repo_dir} "
        f"and once with cwd={worktree_parent}; accept both trust dialogs, then re-run "
        "`kagura-engineer setup --fix headless-permissions`"
    )

    if no_input:
        action = (
            f"{len(missing)} baseline permissions need confirmation; "
            if missing
            else ""
        )
        return result(
            StepStatus.NEEDS_USER,
            action
            + "--no-input cannot confirm Claude workspace trust; no files changed",
            trust_hint,
        )

    if dry_run:
        action = (
            f"would add {len(missing)} baseline permissions to {target}; "
            if missing
            else "allowlist already complete; "
        )
        return result(
            StepStatus.OK,
            "dry-run: "
            + action
            + "would request trust confirmation for both run locations",
            trust_hint,
        )

    if missing:
        prompt = (
            f"Add {len(missing)} missing baseline permissions to {target}? "
            "This widens Claude Code's unattended access. [y/N] "
        )
        if not _confirmed(input_fn, prompt):
            return result(
                StepStatus.NEEDS_USER,
                "permission widening was not confirmed; no files changed",
                "review the baseline in README, then run "
                "`kagura-engineer setup --fix headless-permissions`",
            )

        permissions = settings.setdefault("permissions", {})
        merged_allow = permissions.setdefault("allow", [])
        merged_allow.extend(missing)
        try:
            _write_settings_atomic(target, settings)
        except OSError as exc:
            return result(
                StepStatus.FAIL,
                f"could not write {target}: {type(exc).__name__}: {exc}",
                "ensure the repository .claude directory is writable",
            )

    trust_prompt = (
        "Open Claude Code once in BOTH locations and accept each workspace trust dialog:\n"
        f"  repo: {repo_dir}\n"
        f"  worktrees (create this directory first if needed): {worktree_parent}\n"
        "Have both locations been trusted? [y/N] "
    )
    if not _confirmed(input_fn, trust_prompt):
        changed = (
            f"added {len(missing)} baseline permissions; "
            if missing
            else "allowlist already complete; "
        )
        return result(
            StepStatus.NEEDS_USER,
            changed + "workspace trust still needs human confirmation",
            trust_hint,
        )

    changed = (
        f"added {len(missing)} baseline permissions"
        if missing
        else "baseline permissions already present"
    )
    return result(
        StepStatus.OK,
        f"{changed}; repo and worktree-parent trust confirmed by user",
    )
