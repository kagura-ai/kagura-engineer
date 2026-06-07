"""HITL gate: turn a gh-issue-driven verdict into proceed/halt.

This gate is fixed ON and stays so: green/yellow proceed, everything else
(red, unknown, missing) halts and surfaces to the human. Defaulting the
unknown case to halt is the safe direction — better to stop and show the
human than to mis-read a verdict and let an autonomous run barrel ahead
(`trust before integration`). `--unattended` does NOT relax this gate; it
only tells the *delegated* gh-issue-driven phase to skip its own inner HITL
prompts on green/yellow. Our final verdict re-check here is unchanged, so an
unattended run still halts on red.
"""
from __future__ import annotations

from dataclasses import dataclass

_PROCEED = {"green", "yellow"}


@dataclass(frozen=True)
class GateDecision:
    proceed: bool
    verdict: str


def evaluate(verdict: str | None) -> GateDecision:
    v = (verdict or "").strip().lower()
    if v in _PROCEED:
        return GateDecision(True, v)
    return GateDecision(False, v or "unknown")
