import json

from kagura_engineer.doctor import render
from kagura_engineer.doctor.result import CheckResult, Status


def test_to_json_shape():
    results = [
        CheckResult("git", Status.OK, "inside a git work tree"),
        CheckResult("gh", Status.FAIL, "not authenticated", "gh auth login"),
    ]
    out = json.loads(render.to_json(results))
    assert out["overall"] == "fail"
    assert out["checks"][1] == {
        "name": "gh",
        "status": "fail",
        "detail": "not authenticated",
        "fix_hint": "gh auth login",
    }


def test_print_table_smoke(capsys):
    render.print_table([CheckResult("git", Status.OK, "ok")])
    captured = capsys.readouterr()
    assert "git" in captured.out
