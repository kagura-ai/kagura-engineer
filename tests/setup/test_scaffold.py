"""Tests for setup.scaffold — repo.yaml + .gitignore scaffolding (issue #35)."""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest
import yaml

from kagura_engineer.config import Config
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

    def test_gitignore_failure_does_not_leave_unignored_repo_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # issue #43 item 3: fail-secure ordering. If the .gitignore write fails,
        # repo.yaml must NOT have been written — otherwise a secret-bearing file
        # (a user who later fills creds) is left on disk un-ignored. Matches the
        # memory-mcp gitignore-before-write discipline.
        def _boom(*a: object, **k: object) -> bool:
            raise OSError("cannot write .gitignore")

        monkeypatch.setattr(scaffold, "ensure_gitignore_entry", _boom)
        with pytest.raises(OSError):
            scaffold.scaffold(tmp_path)
        assert not (tmp_path / "repo.yaml").exists()


class TestNameParamCollapsed:
    # issue #43 item 5: the `name` kwarg on ensure_repo_yaml/scaffold was dead
    # (never overridden — the CLI hardcodes repo.yaml via _CONFIG_OPT). It is
    # collapsed to the module constant REPO_YAML_NAME.
    def test_repo_yaml_name_constant_is_repo_yaml(self) -> None:
        assert scaffold.REPO_YAML_NAME == "repo.yaml"

    def test_ensure_repo_yaml_has_no_name_param(self) -> None:
        assert "name" not in inspect.signature(scaffold.ensure_repo_yaml).parameters

    def test_scaffold_has_no_name_param(self) -> None:
        assert "name" not in inspect.signature(scaffold.scaffold).parameters


class TestTemplateConfigRoundTrip:
    # issue #43 item 1(a): the template is a hand-written string literal while
    # config.py is the source of truth. Guard against drift — a renamed/added
    # Config field that the template forgets (or a typo'd template key) is
    # caught here instead of surfacing as a confusing validator error later.
    def test_template_keys_are_subset_of_config_fields(self) -> None:
        data = yaml.safe_load(scaffold.REPO_YAML_TEMPLATE)
        assert set(data).issubset(set(Config.model_fields)), (
            f"template keys not in Config: {set(data) - set(Config.model_fields)}"
        )

    def test_template_local_variant_round_trips_through_config(self) -> None:
        # The template ships memory_backend: cloud with empty creds (won't
        # validate unedited). The local variant — the only form that validates
        # with no edits — must round-trip through Config.model_validate.
        data = yaml.safe_load(scaffold.REPO_YAML_TEMPLATE)
        data["memory_backend"] = "local"
        cfg = Config.model_validate(data)
        assert cfg.memory_backend == "local"
        assert cfg.profile == data["profile"]

    def test_template_cloud_variant_validates_when_creds_filled(self) -> None:
        # The shipped (cloud) backend validates once the user fills the creds.
        data = yaml.safe_load(scaffold.REPO_YAML_TEMPLATE)
        data["memory_cloud_url"] = "https://memory.example"
        data["workspace_id"] = "ws"
        data["context_id"] = "ctx"
        cfg = Config.model_validate(data)
        assert cfg.memory_backend == "cloud"
