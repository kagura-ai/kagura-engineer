"""Parse the kagura-code-reviewer JSON envelope.

The reviewer's contract (schema_version 1):

    {schema_version, verdict, summary{total,blocking,by_severity,incomplete}, findings[]}

We read JSON only — never scrape Markdown. Parsing is deliberately
defensive: any malformed / absent / wrong-typed input degrades to
`parsed=False, verdict=None`, which the gate treats as a halt (safe side).
`SCHEMA_VERSION` is recorded but not enforced — a future bump is read
best-effort so an actor on an older build still gets the verdict.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .result import Finding

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ReviewEnvelope:
    parsed: bool
    verdict: str | None = None
    schema_version: int | None = None
    summary: dict = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)

    @property
    def incomplete(self) -> bool:
        return bool(self.summary.get("incomplete"))

    @classmethod
    def from_text(cls, text: str) -> "ReviewEnvelope":
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            return cls(parsed=False)
        if not isinstance(data, dict):
            return cls(parsed=False)

        verdict = data.get("verdict")
        verdict = verdict.strip().lower() if isinstance(verdict, str) else None

        summary = data.get("summary")
        summary = summary if isinstance(summary, dict) else {}

        raw = data.get("findings")
        findings: list[Finding] = []
        if isinstance(raw, list):
            for f in raw:
                if not isinstance(f, dict):
                    continue
                findings.append(
                    Finding(
                        dimension=str(f.get("dimension", "general")),
                        severity=str(f.get("severity", "INFO")),
                        file=str(f.get("file", "")),
                        line=f.get("line") if isinstance(f.get("line"), int) else None,
                        title=str(f.get("title", "")),
                    )
                )

        sv = data.get("schema_version")
        return cls(
            parsed=True,
            verdict=verdict,
            schema_version=sv if isinstance(sv, int) else None,
            summary=summary,
            findings=findings,
        )
