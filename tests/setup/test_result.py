from kagura_engineer.setup.result import SetupReport, StepResult, StepStatus


def test_step_result_defaults():
    r = StepResult(name="git", status=StepStatus.OK, detail="already installed")
    assert r.fix_hint is None
    assert r.duration_s == 0.0


def test_setup_report_buckets_are_independent():
    ran = [StepResult("a", StepStatus.OK, "ok")]
    skipped = [StepResult("b", StepStatus.SKIPPED, "no work")]
    failed = [StepResult("c", StepStatus.FAIL, "boom")]
    needs_user = [StepResult("d", StepStatus.NEEDS_USER, "please run gh auth login")]
    r = SetupReport(
        ran=ran, skipped=skipped, failed=failed, needs_user=needs_user
    )
    assert r.ran == ran
    assert r.skipped == skipped
    assert r.failed == failed
    assert r.needs_user == needs_user


def test_setup_report_is_blocked_when_fail_present():
    r = SetupReport(failed=[StepResult("c", StepStatus.FAIL, "boom")])
    assert r.is_blocked is True


def test_setup_report_is_blocked_when_needs_user_present():
    r = SetupReport(needs_user=[StepResult("d", StepStatus.NEEDS_USER, "log in")])
    assert r.is_blocked is True


def test_setup_report_is_not_blocked_for_clean_run():
    r = SetupReport(ran=[StepResult("a", StepStatus.OK, "ok")])
    assert r.is_blocked is False


def test_setup_report_empty_is_not_blocked():
    # A report that hasn't run yet (e.g. `--dry-run` abort before any step)
    # must not look blocked.
    r = SetupReport()
    assert r.is_blocked is False
