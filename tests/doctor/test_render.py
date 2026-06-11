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


def test_to_json_keeps_non_ascii():
    r = [CheckResult("memory-cloud", Status.OK, "到達 https://例え.jp")]
    out = render.to_json(r)
    assert "到達" in out


def test_to_json_carries_profile_object():
    # issue #70: `doctor --json` gains a top-level "profile" object.
    prof = {"brain_backend": "claude", "memory_backend": "cloud"}
    out = json.loads(render.to_json([CheckResult("git", Status.OK, "ok")], profile=prof))
    assert out["profile"] == prof


def test_to_json_profile_defaults_to_none():
    out = json.loads(render.to_json([CheckResult("git", Status.OK, "ok")]))
    assert out["profile"] is None
