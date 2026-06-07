from kagura_engineer.run.result import PhaseResult, RunReport, RunStatus


def test_status_values():
    assert RunStatus.OK.value == "ok"
    assert RunStatus.BLOCKED.value == "blocked"
    assert RunStatus.FAIL.value == "fail"


def test_report_status_is_worst_phase():
    ok = PhaseResult("recall", RunStatus.OK, "done")
    blocked = PhaseResult("start", RunStatus.BLOCKED, "red verdict", verdict="red")
    failed = PhaseResult("ship", RunStatus.FAIL, "claude exited 1")

    assert RunReport(issue=1, phases=[ok]).status is RunStatus.OK
    assert RunReport(issue=1, phases=[ok, blocked]).status is RunStatus.BLOCKED
    assert RunReport(issue=1, phases=[ok, blocked, failed]).status is RunStatus.FAIL


def test_empty_report_is_ok():
    assert RunReport(issue=1).status is RunStatus.OK


def test_phase_result_defaults():
    p = PhaseResult("recall", RunStatus.OK, "done")
    assert p.verdict is None
    assert p.duration_s == 0.0
