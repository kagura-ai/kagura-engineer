"""Tests for setup.scaffold — repo.yaml + .gitignore scaffolding (issue #35)."""
from __future__ import annotations

from pathlib import Path

import yaml

from kagura_engineer.setup import scaffold


class TestEnsureGitignoreEntry:
    def test_creates_gitignore_when_absent(self, tmp_path: Path) -> None:
        wrote = scaffold.ensure_gitignore_entry(tmp_path, "repo.yaml", label="kagura-engineer local dev config")
        assert wrote is True
        gi = (tmp_path / ".gitignore").read_text()
        assert "# kagura-engineer local dev config" in gi
        assert "repo.yaml" in gi

    def test_appends_preserving_existing_content(self, tmp_path: Path) -> None:
        gi = tmp_path / ".gitignore"
        gi.write_text("__pycache__/\n*.pyc\n")
        wrote = scaffold.ensure_gitignore_entry(tmp_path, "repo.yaml", label="kagura-engineer local dev config")
        assert wrote is True
        text = gi.read_text()
        assert "__pycache__/" in text and "*.pyc" in text  # existing lines kept
        assert "repo.yaml" in text

    def test_idempotent_when_entry_already_present(self, tmp_path: Path) -> None:
        gi = tmp_path / ".gitignore"
        gi.write_text("# my stuff\nrepo.yaml\n")
        before = gi.read_text()
        wrote = scaffold.ensure_gitignore_entry(tmp_path, "repo.yaml", label="kagura-engineer local dev config")
        assert wrote is False  # no-op
        assert gi.read_text() == before  # unchanged

    def test_present_without_label_still_counts_as_present(self, tmp_path: Path) -> None:
        # The skip check matches the literal entry line, not the label comment —
        # a user who has the bare `repo.yaml` line must not get a duplicate.
        gi = tmp_path / ".gitignore"
        gi.write_text("repo.yaml\n")
        wrote = scaffold.ensure_gitignore_entry(tmp_path, "repo.yaml", label="kagura-engineer local dev config")
        assert wrote is False
        assert gi.read_text().count("repo.yaml") == 1

    def test_substring_or_comment_line_does_not_count_as_present(self, tmp_path: Path) -> None:
        # The skip check is an EXACT line match, so a longer line containing the
        # entry as a substring (`repo.yaml.bak`) or a comment mentioning it must
        # NOT cause a false-skip — the false-skip direction is the dangerous one
        # (it would leave the real entry un-ignored).
        gi = tmp_path / ".gitignore"
        gi.write_text("repo.yaml.bak\n# old repo.yaml note\n")
        wrote = scaffold.ensure_gitignore_entry(tmp_path, "repo.yaml", label="kagura-engineer local dev config")
        assert wrote is True
        assert "repo.yaml" in gi.read_text().splitlines()  # the exact entry now present

    def test_tolerates_missing_trailing_newline(self, tmp_path: Path) -> None:
        # An existing file without a trailing newline must not concatenate onto
        # the last line (e.g. "*.log" + "repo.yaml" -> "*.logrepo.yaml").
        gi = tmp_path / ".gitignore"
        gi.write_text("*.log")  # no trailing newline
        scaffold.ensure_gitignore_entry(tmp_path, "repo.yaml", label="kagura-engineer local dev config")
        lines = gi.read_text().splitlines()
        assert "*.log" in lines
        assert "repo.yaml" in lines


class TestEnsureRepoYaml:
    def test_writes_template_when_absent(self, tmp_path: Path) -> None:
        wrote = scaffold.ensure_repo_yaml(tmp_path)
        assert wrote is True
        text = (tmp_path / "repo.yaml").read_text()
        # The template documents the real Config fields a user must fill in.
        for field in ("profile", "memory_backend", "workspace_id", "context_id"):
            assert field in text

    def test_never_overwrites_existing(self, tmp_path: Path) -> None:
        existing = tmp_path / "repo.yaml"
        existing.write_text("profile: mine\nmemory_backend: local\n")
        wrote = scaffold.ensure_repo_yaml(tmp_path)
        assert wrote is False
        assert existing.read_text() == "profile: mine\nmemory_backend: local\n"

    def test_template_is_valid_yaml(self, tmp_path: Path) -> None:
        scaffold.ensure_repo_yaml(tmp_path)
        # Whatever the template ships, it must parse as YAML (commented lines ok).
        data = yaml.safe_load((tmp_path / "repo.yaml").read_text())
        assert isinstance(data, dict)


class TestScaffold:
    def test_fresh_repo_creates_both(self, tmp_path: Path) -> None:
        result = scaffold.scaffold(tmp_path)
        assert result.repo_yaml_created is True
        assert result.gitignore_updated is True
        assert (tmp_path / "repo.yaml").is_file()
        assert "repo.yaml" in (tmp_path / ".gitignore").read_text()

    def test_rerun_is_idempotent(self, tmp_path: Path) -> None:
        scaffold.scaffold(tmp_path)
        result = scaffold.scaffold(tmp_path)  # second run
        assert result.repo_yaml_created is False
        assert result.gitignore_updated is False
