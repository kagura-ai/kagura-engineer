from kagura_engineer.doctor.result import CheckResult, Status


def test_ok_result_has_no_fix_hint_by_default():
    r = CheckResult(name="git", status=Status.OK, detail="clean tree")
    assert r.status is Status.OK
    assert r.fix_hint is None
    assert r.is_blocking is False


def test_fail_result_is_blocking_and_carries_hint():
    r = CheckResult(
        name="claude-code",
        status=Status.FAIL,
        detail="claude not found on PATH",
        fix_hint="install Claude Code (https://claude.ai/download) and re-run doctor",
    )
    assert r.is_blocking is True
    assert "re-run doctor" in r.fix_hint


def test_warn_result_is_not_blocking():
    r = CheckResult(name="haiku", status=Status.WARN, detail="no auth path verified")
    assert r.is_blocking is False
