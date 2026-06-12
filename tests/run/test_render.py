import json

from kagura_engineer.run.render import to_json
from kagura_engineer.run.result import PhaseResult, RunReport, RunStatus


def test_to_json_shape():
    report = RunReport(
        issue=42,
        phases=[
            PhaseResult("recall", RunStatus.OK, "3 memories"),
            PhaseResult("start", RunStatus.BLOCKED, "red verdict", verdict="red", duration_s=1.2),
        ],
        pr_url=None,
        worktree="/tmp/.kagura-runs/repo/run-42",
        resume_hint="re-run `kagura-engineer run 42`",
        duration_s=2.5,
    )
    data = json.loads(to_json(report))
    assert data["issue"] == 42
    assert data["status"] == "blocked"
    assert data["pr_url"] is None
    assert data["resume_hint"].startswith("re-run")
    assert data["phases"][1]["verdict"] == "red"
    assert data["phases"][1]["duration_s"] == 1.2


def test_print_table_smoke(capsys):
    from kagura_engineer.run.render import print_table

    print_table(RunReport(issue=1, phases=[PhaseResult("ship", RunStatus.OK, "PR opened")], pr_url="https://x/pull/1"))
    out = capsys.readouterr().out
    assert "ship" in out


def test_to_json_carries_profile():
    # issue #70: a report carrying an ExecutionProfile serialises it under
    # the top-level "profile" key (the to_dict form).
    from kagura_engineer.profile import ExecutionProfile
    from tests._constants import EXECUTION_PROFILE_KWARGS

    report = RunReport(issue=1, profile=ExecutionProfile(**EXECUTION_PROFILE_KWARGS))
    data = json.loads(to_json(report))
    assert data["profile"]["brain_backend"] == "claude"
    assert data["profile"]["memory_backend"] == "cloud"


def test_to_json_profile_defaults_to_none():
    data = json.loads(to_json(RunReport(issue=1)))
    assert data["profile"] is None


def test_to_json_review_defaults_to_none():
    # issue #74: a run that never reached the code-review (implement) phase has
    # no review record — null, distinguishable from a run that did review.
    data = json.loads(to_json(RunReport(issue=1)))
    assert data["review"] is None


def test_to_json_carries_review_provider_and_model():
    # issue #74: the actually-used review provider/model is recorded in the JSON.
    from kagura_engineer.profile import ReviewProfile

    report = RunReport(issue=1, review=ReviewProfile(provider="claude", model=None))
    data = json.loads(to_json(report))
    assert data["review"]["provider"] == "claude"
    assert data["review"]["model"] is None
    assert data["review"]["via"] == "brain in-phase /code-review"


def test_print_table_shows_review_line():
    # issue #74 AC2: the human summary shows which provider/model reviewed.
    from kagura_engineer.profile import ReviewProfile
    from kagura_engineer.run.render import print_table

    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_table(RunReport(
            issue=1, phases=[PhaseResult("ship", RunStatus.OK, "PR opened")],
            review=ReviewProfile(provider="claude", model=None),
        ))
    out = buf.getvalue()
    assert "review:" in out
    assert "claude" in out


def test_print_table_shows_review_none_when_no_review():
    # issue #74 AC3: the human summary makes "no review ran" explicit.
    import io
    from contextlib import redirect_stdout

    from kagura_engineer.run.render import print_table

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_table(RunReport(issue=1, phases=[PhaseResult("guard", RunStatus.BLOCKED, "x")]))
    assert "review: none ran" in buf.getvalue()
