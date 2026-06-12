"""Scaffold a per-checkout `repo.yaml` and keep it out of git (issue #35).

`config.py` only *reads* `repo.yaml` and hard-raises when it is missing, and
nothing added it to `.gitignore` — so a fresh checkout starts with no config,
and a hand-authored one carrying `workspace_id`/`context_id` is easy to commit
by accident. The `init` command (and any caller) uses this module to:

  1. write a commented `repo.yaml` template **iff absent** (never overwrites), and
  2. idempotently add `repo.yaml` to `.gitignore` under a labeled block.

`ensure_gitignore_entry` is deliberately generic (entry + label) so the
memory-mcp step can route its generated `.mcp.json` through the same helper —
that file's static-token form carries a bearer key and has the same
keep-it-out-of-git need.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

GITIGNORE_LABEL = "kagura-engineer local dev config"

# The per-checkout config filename. The CLI always reads/writes `repo.yaml`
# (hardcoded via cli._CONFIG_OPT), so this is a fixed module constant rather
# than a parameter — collapsing the previously-dead `name` kwarg (issue #43
# item 5).
REPO_YAML_NAME = "repo.yaml"

# A starting point the user edits — parses as YAML, documents the real Config
# fields. Cloud fields ship empty (required only when memory_backend: cloud, see
# config.py:_require_cloud_fields) so a local-backend checkout needs no edits.
REPO_YAML_TEMPLATE = """\
# kagura-engineer per-checkout config (repo.yaml).
# Local-dev only — git-ignored by `kagura-engineer init`. Fill in the values for
# your checkout, then run `kagura-engineer doctor` to verify.

# Free-form profile label for this checkout (e.g. "dev", "ci").
profile: dev

# Memory backend: "cloud" (Kagura Memory Cloud) or "local" (offline SQLite).
memory_backend: cloud

# --- Cloud backend fields (required when memory_backend: cloud) ---
# Memory Cloud base URL and the workspace/context this repo writes to.
memory_cloud_url: ""
workspace_id: ""
context_id: ""

# --- Local backend (used only when memory_backend: local) ---
# local_memory_path: .kagura/memory.db

# Ollama endpoint for the cost-free reviewer.
ollama_url: "http://localhost:11434"

# --- In-loop code review (run/goal implement phase) ---
# code_review: auto | always | never — whether the brain runs /code-review over
#   the diff (auto = brain decides from diff size / risk / tests; default).
# effort: low | medium | high — the effort hint passed to /code-review.
# review:
#   code_review: auto
#   effort: medium
"""


@dataclass(frozen=True)
class ScaffoldResult:
    repo_yaml_created: bool
    gitignore_updated: bool
    repo_yaml_path: Path
    gitignore_path: Path


def ensure_gitignore_entry(repo_dir: str | Path, entry: str, *, label: str) -> bool:
    """Idempotently add ``entry`` to ``<repo_dir>/.gitignore``.

    Returns ``True`` if the file was written (created or appended), ``False`` if
    ``entry`` was already a line (the skip check matches the literal entry line,
    not the label — a bare pre-existing line must not be duplicated). Creates the
    file when absent and tolerates an existing file with no trailing newline.
    """
    path = Path(repo_dir) / ".gitignore"
    # Pin utf-8 on every text read/write: the OS-default encoding (cp932 on
    # Windows-JP) crashes on a UTF-8 .gitignore carrying a byte invalid in that
    # codec, and would round-trip-corrupt non-ASCII content on write.
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if entry in existing.splitlines():
        return False
    if existing and not existing.endswith("\n"):
        existing += "\n"
    separator = "\n" if existing else ""  # blank line before an appended block
    path.write_text(f"{existing}{separator}# {label}\n{entry}\n", encoding="utf-8")
    return True


def ensure_repo_yaml(repo_dir: str | Path) -> bool:
    """Write the ``repo.yaml`` template iff absent. Returns ``True`` if written.

    Never overwrites an existing file — a populated ``repo.yaml`` holds the
    user's workspace/context IDs and must be preserved.
    """
    path = Path(repo_dir) / REPO_YAML_NAME
    if path.exists():
        return False
    path.write_text(REPO_YAML_TEMPLATE, encoding="utf-8")
    return True


def scaffold(repo_dir: str | Path) -> ScaffoldResult:
    """Scaffold ``repo.yaml`` and add it to ``.gitignore`` (both idempotent).

    Gitignore-first ordering (issue #43 item 3): the ``.gitignore`` entry is
    written *before* ``repo.yaml`` so that if the gitignore write fails, the
    config file is never left on disk un-ignored. This mirrors the memory-mcp
    step's fail-secure "gitignore-before-write-or-refuse" discipline — a user
    who later fills in cloud credentials must never have an un-ignored file.
    """
    root = Path(repo_dir)
    updated = ensure_gitignore_entry(root, REPO_YAML_NAME, label=GITIGNORE_LABEL)
    created = ensure_repo_yaml(root)
    return ScaffoldResult(created, updated, root / REPO_YAML_NAME, root / ".gitignore")
