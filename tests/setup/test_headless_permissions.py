"""Tests for the interactive Claude headless-permissions setup step."""
from __future__ import annotations

import json

from kagura_engineer.setup.headless_permissions import (
    BASELINE_PERMISSIONS,
    ensure_headless_permissions,
)
from kagura_engineer.setup.result import StepStatus


def _answers(*values: str):
    iterator = iter(values)
    return lambda _prompt: next(iterator)


def test_fresh_repo_creates_baseline_after_confirmation(tmp_path, valid_config):
    result = ensure_headless_permissions(
        valid_config,
        no_input=False,
        dry_run=False,
        repo_dir=tmp_path,
        input_fn=_answers("yes", "yes"),
    )

    assert result.status is StepStatus.OK
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert settings["permissions"]["allow"] == list(BASELINE_PERMISSIONS)


def test_merge_preserves_unrelated_settings_and_existing_permissions(
    tmp_path, valid_config
):
    target = tmp_path / ".claude" / "settings.json"
    target.parent.mkdir()
    original = {
        "model": "sonnet",
        "permissions": {
            "allow": ["CustomTool", "Edit"],
            "deny": ["Bash(rm *)"],
            "additionalDirectories": ["../shared"],
        },
        "hooks": {"PreToolUse": []},
    }
    target.write_text(json.dumps(original), encoding="utf-8")

    result = ensure_headless_permissions(
        valid_config,
        no_input=False,
        dry_run=False,
        repo_dir=tmp_path,
        input_fn=_answers("y", "y"),
    )

    assert result.status is StepStatus.OK
    merged = json.loads(target.read_text())
    assert merged["model"] == "sonnet"
    assert merged["hooks"] == original["hooks"]
    assert merged["permissions"]["deny"] == ["Bash(rm *)"]
    assert merged["permissions"]["additionalDirectories"] == ["../shared"]
    assert merged["permissions"]["allow"][:2] == ["CustomTool", "Edit"]
    assert merged["permissions"]["allow"].count("Edit") == 1
    assert set(BASELINE_PERMISSIONS) <= set(merged["permissions"]["allow"])


def test_second_run_is_file_idempotent(tmp_path, valid_config):
    target = tmp_path / ".claude" / "settings.json"
    first = ensure_headless_permissions(
        valid_config,
        no_input=False,
        dry_run=False,
        repo_dir=tmp_path,
        input_fn=_answers("yes", "yes"),
    )
    first_bytes = target.read_bytes()

    second = ensure_headless_permissions(
        valid_config,
        no_input=False,
        dry_run=False,
        repo_dir=tmp_path,
        input_fn=_answers("yes"),
    )

    assert first.status is StepStatus.OK
    assert second.status is StepStatus.OK
    assert target.read_bytes() == first_bytes


def test_refusal_does_not_create_settings(tmp_path, valid_config):
    result = ensure_headless_permissions(
        valid_config,
        no_input=False,
        dry_run=False,
        repo_dir=tmp_path,
        input_fn=_answers("no"),
    )

    assert result.status is StepStatus.NEEDS_USER
    assert not (tmp_path / ".claude").exists()


def test_no_input_never_mutates(tmp_path, valid_config):
    result = ensure_headless_permissions(
        valid_config,
        no_input=True,
        dry_run=False,
        repo_dir=tmp_path,
        input_fn=lambda _prompt: (_ for _ in ()).throw(
            AssertionError("must not prompt")
        ),
    )

    assert result.status is StepStatus.NEEDS_USER
    assert "no files changed" in result.detail
    assert not (tmp_path / ".claude").exists()


def test_no_input_preserves_existing_settings_bytes(tmp_path, valid_config):
    target = tmp_path / ".claude" / "settings.json"
    target.parent.mkdir()
    target.write_text('{"custom": true}\n', encoding="utf-8")
    before = target.read_bytes()

    result = ensure_headless_permissions(
        valid_config,
        no_input=True,
        dry_run=False,
        repo_dir=tmp_path,
    )

    assert result.status is StepStatus.NEEDS_USER
    assert target.read_bytes() == before


def test_dry_run_previews_without_prompt_or_mutation(tmp_path, valid_config):
    result = ensure_headless_permissions(
        valid_config,
        no_input=False,
        dry_run=True,
        repo_dir=tmp_path,
        input_fn=lambda _prompt: (_ for _ in ()).throw(
            AssertionError("must not prompt")
        ),
    )

    assert result.status is StepStatus.OK
    assert "dry-run" in result.detail
    assert not (tmp_path / ".claude").exists()


def test_codex_skips_before_touching_claude_settings(tmp_path, valid_config):
    target = tmp_path / ".claude" / "settings.json"
    target.parent.mkdir()
    target.write_text("not json", encoding="utf-8")
    cfg = valid_config.model_copy(update={"brain_backend": "codex"})

    result = ensure_headless_permissions(
        cfg,
        no_input=False,
        dry_run=False,
        repo_dir=tmp_path,
        input_fn=lambda _prompt: (_ for _ in ()).throw(
            AssertionError("must not prompt")
        ),
    )

    assert result.status is StepStatus.SKIPPED
    assert "codex" in result.detail
    assert target.read_text() == "not json"


def test_invalid_existing_shape_fails_without_overwrite(tmp_path, valid_config):
    target = tmp_path / ".claude" / "settings.json"
    target.parent.mkdir()
    target.write_text('{"permissions": {"allow": "Edit"}}', encoding="utf-8")

    result = ensure_headless_permissions(
        valid_config,
        no_input=False,
        dry_run=False,
        repo_dir=tmp_path,
        input_fn=_answers("yes"),
    )

    assert result.status is StepStatus.FAIL
    assert "JSON array" in result.detail
    assert target.read_text() == '{"permissions": {"allow": "Edit"}}'


def test_null_permissions_fails_without_overwrite(tmp_path, valid_config):
    target = tmp_path / ".claude" / "settings.json"
    target.parent.mkdir()
    target.write_text('{"permissions": null}', encoding="utf-8")

    result = ensure_headless_permissions(
        valid_config,
        no_input=False,
        dry_run=False,
        repo_dir=tmp_path,
        input_fn=_answers("yes"),
    )

    assert result.status is StepStatus.FAIL
    assert target.read_text() == '{"permissions": null}'


def test_symlinked_settings_is_refused(tmp_path, valid_config):
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    target = tmp_path / "repo" / ".claude" / "settings.json"
    target.parent.mkdir(parents=True)
    try:
        target.symlink_to(outside)
    except OSError:
        return  # creating symlinks can require elevated privileges on Windows

    result = ensure_headless_permissions(
        valid_config,
        no_input=False,
        dry_run=False,
        repo_dir=tmp_path / "repo",
        input_fn=_answers("yes"),
    )

    assert result.status is StepStatus.FAIL
    assert outside.read_text() == "{}"


def test_permission_write_can_finish_with_trust_needs_user(tmp_path, valid_config):
    result = ensure_headless_permissions(
        valid_config,
        no_input=False,
        dry_run=False,
        repo_dir=tmp_path,
        input_fn=_answers("yes", "no"),
    )

    assert result.status is StepStatus.NEEDS_USER
    assert "workspace trust" in result.detail
    assert (tmp_path / ".claude" / "settings.json").is_file()
    assert ".kagura-runs" in (result.fix_hint or "")
