import pytest

from kagura_engineer.run.gate import GateDecision, evaluate


@pytest.mark.parametrize("verdict", ["green", "GREEN", "yellow", "Yellow"])
def test_green_and_yellow_proceed(verdict):
    d = evaluate(verdict)
    assert isinstance(d, GateDecision)
    assert d.proceed is True


@pytest.mark.parametrize("verdict", ["red", "RED", "", "  ", None, "garbage"])
def test_red_unknown_and_missing_halt(verdict):
    d = evaluate(verdict)
    assert d.proceed is False


def test_decision_records_normalized_verdict():
    assert evaluate("GREEN").verdict == "green"
    assert evaluate(None).verdict == "unknown"
