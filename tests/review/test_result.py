from kagura_engineer.review.result import (
    Finding,
    ReviewReport,
    ReviewStatus,
)


def test_status_is_worst_of_components():
    r = ReviewReport(target="HEAD", base="main", verdict="green", status=ReviewStatus.OK)
    assert r.status is ReviewStatus.OK


def test_blocked_beats_ok_via_explicit_status():
    r = ReviewReport(target="HEAD", base="main", verdict="red", status=ReviewStatus.BLOCKED)
    assert r.status is ReviewStatus.BLOCKED


def test_finding_holds_surface_fields():
    f = Finding(dimension="security", severity="HIGH", file="a.py", line=12, title="SQLi")
    assert f.severity == "HIGH"
    assert f.file == "a.py"
    assert f.line == 12
