from kagura_engineer.setup.render import print_table, to_json
from kagura_engineer.setup.result import SetupReport, StepResult, StepStatus


def _report() -> SetupReport:
    return SetupReport(
        ran=[StepResult("git", StepStatus.OK, "skipped (already on PATH)", duration_s=0.1)],
        skipped=[StepResult("ollama-models", StepStatus.SKIPPED, "no models configured")],
        needs_user=[
            StepResult(
                "gh",
                StepStatus.NEEDS_USER,
                "gh auth not detected",
                fix_hint="run `gh auth login`",
            )
        ],
        failed=[],
        duration_s=0.5,
    )


def test_to_json_shape_and_buckets():
    out = to_json(_report())
    import json

    data = json.loads(out)
    assert set(data) == {
        "ran",
        "skipped",
        "needs_user",
        "failed",
        "duration_s",
        "is_blocked",
    }
    assert data["ran"][0]["name"] == "git"
    assert data["ran"][0]["status"] == "ok"
    assert data["ran"][0]["duration_s"] == 0.1
    assert data["needs_user"][0]["fix_hint"] == "run `gh auth login`"
    assert data["is_blocked"] is True
    assert data["duration_s"] == 0.5


def test_to_json_is_blocked_false_when_clean():
    report = SetupReport(ran=[StepResult("git", StepStatus.OK, "ok")])
    import json

    data = json.loads(to_json(report))
    assert data["is_blocked"] is False
    assert data["failed"] == []
    assert data["needs_user"] == []


def test_print_table_does_not_raise(capsys):
    # Smoke check: the table renders without raising. Rich writes to stdout
    # via Console; capsys captures both streams.
    print_table(_report())
    captured = capsys.readouterr()
    assert "git" in captured.out
    assert "gh" in captured.out
    # The four bucket labels show up via the status column.
    for s in ("ok", "skipped", "needs_user"):
        assert s in captured.out
