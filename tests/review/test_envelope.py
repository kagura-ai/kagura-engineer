import json

from kagura_engineer.review.envelope import ReviewEnvelope


def _payload(verdict="green", findings=None, incomplete=False):
    findings = findings or []
    return json.dumps(
        {
            "schema_version": 1,
            "verdict": verdict,
            "summary": {
                "total": len(findings),
                "blocking": sum(1 for f in findings if f.get("severity") in ("HIGH", "CRITICAL")),
                "by_severity": {},
                "incomplete": incomplete,
            },
            "findings": findings,
        }
    )


def test_parses_green_envelope():
    env = ReviewEnvelope.from_text(_payload("green"))
    assert env.verdict == "green"
    assert env.parsed is True
    assert env.incomplete is False
    assert env.findings == []


def test_parses_red_with_findings():
    env = ReviewEnvelope.from_text(
        _payload("red", [{"dimension": "security", "severity": "HIGH",
                          "file": "a.py", "line": 3, "title": "SQLi"}])
    )
    assert env.verdict == "red"
    assert env.findings[0].file == "a.py"
    assert env.findings[0].severity == "HIGH"
    assert env.summary["blocking"] == 1


def test_invalid_json_yields_unparsed_none_verdict():
    env = ReviewEnvelope.from_text("not json {")
    assert env.parsed is False
    assert env.verdict is None


def test_empty_text_yields_unparsed():
    env = ReviewEnvelope.from_text("")
    assert env.parsed is False
    assert env.verdict is None


def test_missing_verdict_field_is_none_but_parsed():
    env = ReviewEnvelope.from_text(json.dumps({"schema_version": 1, "findings": []}))
    assert env.parsed is True
    assert env.verdict is None


def test_incomplete_flag_read_from_summary():
    env = ReviewEnvelope.from_text(_payload("yellow", incomplete=True))
    assert env.incomplete is True


def test_unknown_schema_version_recorded():
    env = ReviewEnvelope.from_text(json.dumps({"schema_version": 99, "verdict": "green"}))
    assert env.schema_version == 99
    assert env.verdict == "green"


def test_non_list_findings_tolerated():
    env = ReviewEnvelope.from_text(json.dumps({"verdict": "green", "findings": "oops"}))
    assert env.findings == []
