import json

from kagura_engineer.review.render import print_table, to_json
from kagura_engineer.review.result import Finding, ReviewReport, ReviewStatus


def _report():
    return ReviewReport(
        target="feat/x", base="main", verdict="red", status=ReviewStatus.BLOCKED,
        summary={"total": 1, "blocking": 1},
        findings=[Finding("security", "HIGH", "a.py", 3, "SQLi")],
        detail="blocking verdict (red): 1 finding(s)",
        resume_hint="address the findings",
    )


def test_to_json_roundtrips():
    data = json.loads(to_json(_report()))
    assert data["verdict"] == "red"
    assert data["status"] == "blocked"
    assert data["findings"][0]["file"] == "a.py"
    assert data["findings"][0]["severity"] == "HIGH"
    assert data["summary"]["blocking"] == 1


def test_print_table_runs(capsys):
    print_table(_report())
    out = capsys.readouterr().out
    assert "SQLi" in out
    assert "red" in out
