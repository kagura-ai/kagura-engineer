from kagura_engineer.review.result import (
    Finding,
    ReviewReport,
    ReviewStatus,
)


def test_status_field_roundtrips():
    r = ReviewReport(target="HEAD", base="main", verdict="green", status=ReviewStatus.OK)
    assert r.status is ReviewStatus.OK


def test_report_records_blocked_status():
    r = ReviewReport(target="HEAD", base="main", verdict="red", status=ReviewStatus.BLOCKED)
    assert r.status is ReviewStatus.BLOCKED


def test_finding_holds_surface_fields():
    f = Finding(dimension="security", severity="HIGH", file="a.py", line=12, title="SQLi")
    assert f.severity == "HIGH"
    assert f.file == "a.py"
    assert f.line == 12


def test_report_defaults():
    r = ReviewReport(target="HEAD", base="main")
    assert r.verdict is None
    assert r.status is ReviewStatus.OK
    assert r.summary == {}
    assert r.findings == []
    assert r.detail == ""
    assert r.resume_hint is None
    assert r.report_path is None
    assert r.duration_s == 0.0
