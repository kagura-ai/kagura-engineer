import subprocess
from pathlib import Path

from kagura_engineer.review import reviewer
from kagura_engineer.review.reviewer import ReviewerResult


def test_build_argv_core_flags(tmp_path):
    out = tmp_path / "r.json"
    argv = reviewer.build_argv(
        base="main", head="HEAD", repo=Path("."), out=out,
        context_file=None, model=None, effort="med",
    )
    assert argv[0] == "kagura-code-reviewer"
    assert "--format" in argv and "json" in argv
    assert "--base" in argv and "main" in argv
    assert "--head" in argv and "HEAD" in argv
    assert "--out" in argv and str(out) in argv
    assert "--effort" in argv and "med" in argv
    assert "--context-file" not in argv
    assert "--model" not in argv


def test_build_argv_includes_optionals(tmp_path):
    ctx = tmp_path / "ctx.md"
    argv = reviewer.build_argv(
        base="main", head="HEAD", repo=Path("."), out=tmp_path / "r.json",
        context_file=ctx, model="review-local", effort="high",
    )
    assert "--context-file" in argv and str(ctx) in argv
    assert "--model" in argv and "review-local" in argv


def test_resolve_head_passes_through_branch():
    assert reviewer.resolve_head("feat/x") == "feat/x"


def test_resolve_head_resolves_pr_number(monkeypatch):
    def _fake_run(cmd, **kw):
        assert cmd[:3] == ["gh", "pr", "view"]
        return subprocess.CompletedProcess(cmd, 0, "feat/from-pr\n", "")
    monkeypatch.setattr(reviewer.subprocess, "run", _fake_run)
    assert reviewer.resolve_head("42") == "feat/from-pr"


def test_resolve_head_pr_number_falls_back_on_gh_error(monkeypatch):
    def _boom(cmd, **kw):
        raise subprocess.CalledProcessError(1, cmd)
    monkeypatch.setattr(reviewer.subprocess, "run", _boom)
    assert reviewer.resolve_head("42") == "42"


def test_run_reviewer_reads_out_file(monkeypatch, tmp_path):
    out = tmp_path / "r.json"

    def _fake_run(cmd, **kw):
        Path(cmd[cmd.index("--out") + 1]).write_text('{"schema_version":1,"verdict":"green","findings":[]}')
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(reviewer.subprocess, "run", _fake_run)
    res = reviewer.run_reviewer(base="main", head="HEAD", repo=tmp_path, out=out)
    assert isinstance(res, ReviewerResult)
    assert res.returncode == 0
    assert res.envelope.verdict == "green"
    assert res.no_changes is False


def test_run_reviewer_red_exit_one(monkeypatch, tmp_path):
    out = tmp_path / "r.json"

    def _fake_run(cmd, **kw):
        Path(cmd[cmd.index("--out") + 1]).write_text(
            '{"schema_version":1,"verdict":"red","summary":{"blocking":1},"findings":[]}'
        )
        return subprocess.CompletedProcess(cmd, 1, "", "")

    monkeypatch.setattr(reviewer.subprocess, "run", _fake_run)
    res = reviewer.run_reviewer(base="main", head="HEAD", repo=tmp_path, out=out)
    assert res.returncode == 1
    assert res.envelope.verdict == "red"


def test_run_reviewer_detects_no_changes(monkeypatch, tmp_path):
    def _fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, "No changes to review.\n", "")

    monkeypatch.setattr(reviewer.subprocess, "run", _fake_run)
    res = reviewer.run_reviewer(base="main", head="HEAD", repo=tmp_path, out=tmp_path / "r.json")
    assert res.no_changes is True
    assert res.envelope.parsed is False


def test_run_reviewer_infra_exit_unparsed(monkeypatch, tmp_path):
    def _fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 2, "", "git diff failed")

    monkeypatch.setattr(reviewer.subprocess, "run", _fake_run)
    res = reviewer.run_reviewer(base="main", head="HEAD", repo=tmp_path, out=tmp_path / "r.json")
    assert res.returncode == 2
    assert res.envelope.parsed is False
    assert "git diff failed" in res.stderr


def test_run_reviewer_timeout(monkeypatch, tmp_path):
    def _raise(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(reviewer.subprocess, "run", _raise)
    res = reviewer.run_reviewer(base="main", head="HEAD", repo=tmp_path, out=tmp_path / "r.json")
    assert res.timed_out is True
    assert res.returncode == -1
