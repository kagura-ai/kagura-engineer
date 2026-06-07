import subprocess
from pathlib import Path

import pytest

from kagura_engineer.run import worktree


def test_worktree_path_is_outside_repo_and_named_by_issue(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    p = worktree.worktree_path(repo, 42)
    assert p.name == "run-42"
    assert "myrepo" in str(p)
    assert repo not in p.parents  # lives in a sibling .kagura-runs tree, not inside the repo


def test_ensure_worktree_resumes_when_path_exists(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    existing = worktree.worktree_path(repo, 7)
    existing.mkdir(parents=True)
    called = []
    monkeypatch.setattr(worktree.subprocess, "run", lambda *a, **k: called.append(a))
    out = worktree.ensure_worktree(repo, 7)
    assert out == existing
    assert called == []  # resume path: no git invocation


def test_ensure_worktree_creates_when_absent(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    cmds = []

    def _fake_run(cmd, **kw):
        cmds.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(worktree.subprocess, "run", _fake_run)
    out = worktree.ensure_worktree(repo, 9, base="main")
    assert out == worktree.worktree_path(repo, 9)
    assert cmds[0][:3] == ["git", "worktree", "add"]
    assert "main" in cmds[0]


def test_ensure_worktree_raises_on_git_failure(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(
        worktree.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "fatal: bad base"),
    )
    with pytest.raises(worktree.WorktreeError):
        worktree.ensure_worktree(repo, 9)


def test_remove_worktree_calls_git_remove_force(tmp_path, monkeypatch):
    cmds = []
    monkeypatch.setattr(
        worktree.subprocess, "run",
        lambda cmd, **kw: cmds.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    worktree.remove_worktree(Path("/tmp/run-1"))
    assert cmds[0] == ["git", "worktree", "remove", "--force", "/tmp/run-1"]
