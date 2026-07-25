"""Harness-side issue acquisition and task-echo verification (issue #92).

Why this module exists
----------------------
The phase prompts used to name the issue only by *number* — "Implement GitHub
issue #42" — and left the actual acquisition to the model, expecting it to run
`gh issue view` itself. Claude does. Local/OSS brains frequently do not: a
32-run campaign (qwen3-coder-30b via the codex backend) reached a PR five times
and **every one of those PRs implemented something unrelated to its assigned
issue** — move-history display for "toggle persistence", a chess fix for a
tetris issue. The recurring motifs came from salient repository context, so the
model was improvising a task rather than reading the assigned one. A blind
3-judge panel scored all 14 submitted diffs 0 against their issues.

Two harness-side seams close that gap, and both live here:

1. :func:`fetch_issue` + :meth:`IssueBrief.as_prompt_block` — the harness runs
   `gh issue view` itself and injects the title and body **verbatim** into every
   phase prompt. Acquisition stops being a model behaviour and becomes a
   harness guarantee.

2. :func:`task_echo_matches` — the start phase is asked to restate the assigned
   task in one line (the `KAGURA_TASK=` marker); the orchestrator compares that
   restatement against the issue before dispatching implement. A model that has
   silently substituted a different task says so in its own echo, and the run
   halts *before* burning a 30-minute implement phase on the wrong work.

Trust note. The issue body is injected as the task the run was launched to
perform, which is exactly the trust level it already had when the model fetched
it with `gh issue view`. This is deliberately NOT the "untrusted reference"
framing applied to recalled memory: recalled memory is background context that
must never issue instructions, whereas the issue *is* the instruction. The
delimiters below bound the block so body text cannot be mistaken for the
harness's own directives.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .._launch import run_text

_log = logging.getLogger(__name__)

# `gh issue view` is a network round-trip; bounded so an unreachable/wedged gh
# cannot stall the run before its first phase.
_FETCH_TIMEOUT_S = 30

# Injected verbatim into every phase prompt, so an essay-length issue body must
# not crowd out the grounding and directives around it. Generous enough that a
# normal issue (including tables and code blocks) survives whole.
_BODY_CAP = 6000
_TRUNCATION_NOTE = "\n\n…(body truncated by kagura-engineer at {cap} characters)"


@dataclass(frozen=True)
class IssueBrief:
    """The assigned issue as the harness read it from GitHub."""

    number: int
    title: str
    body: str

    def as_prompt_block(self) -> str:
        """The verbatim, delimited issue block injected into a phase prompt.

        Delimiters are load-bearing: they mark where harness directives stop and
        third-party issue text begins, so a body containing imperative prose
        reads as *the task description* rather than as new instructions to the
        harness.
        """
        head = (
            f"--- BEGIN ISSUE #{self.number} (verbatim, authoritative) ---\n"
            f"Title: {self.title}\n"
        )
        body = f"\n{self.body}\n" if self.body else ""
        return f"{head}{body}--- END ISSUE #{self.number} ---\n"


def fetch_issue(issue: int, cwd: Path | str) -> IssueBrief | None:
    """Read issue `issue` from GitHub, or None if it cannot be read.

    Best-effort by design: `gh` missing, unauthenticated, timing out, or
    emitting nonconforming JSON all degrade to None, which leaves the phase
    prompts exactly as they were before this feature (issue number only). A
    failure here must never fail a run — it only forfeits the #92 hardening.
    """
    try:
        proc = run_text(
            ["gh", "issue", "view", str(issue), "--json", "number,title,body"],
            cwd=str(cwd), capture_output=True, timeout=_FETCH_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
        _log.exception("could not fetch issue #%s (gh unavailable)", issue)
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    title = data.get("title")
    # A brief with no title can anchor neither the prompt block nor the echo
    # gate, so it is worth nothing — report absence rather than a hollow brief.
    if not isinstance(title, str) or not title.strip():
        return None
    # `gh` emits `"body": null` for an issue opened with no description.
    raw_body = data.get("body")
    body = raw_body if isinstance(raw_body, str) else ""
    if len(body) > _BODY_CAP:
        body = body[:_BODY_CAP] + _TRUNCATION_NOTE.format(cap=_BODY_CAP)
    number = data.get("number")
    # Fall back to the requested number so a nonconforming response can never
    # desync the brief from the issue this run was launched for.
    if not isinstance(number, int):
        number = issue
    return IssueBrief(number=number, title=title.strip(), body=body.strip())


# --------------------------------------------------------------------------
# Task-echo matching
# --------------------------------------------------------------------------

# Latin/digit words shorter than this carry no discriminating signal ("ai", "a",
# "to"), and including them would let grammatical noise satisfy the gate.
_MIN_TOKEN_LEN = 3

# Closed-class words only — articles, conjunctions, prepositions, auxiliaries,
# determiners. Deliberately excludes anything that could be a domain term (a
# stopword list that swallowed "new" or "run" would blind the gate to real
# titles), so the omissions here are as load-bearing as the entries.
_STOPWORDS = frozenset({
    "the", "and", "but", "for", "not", "with", "that", "this", "from", "into",
    "onto", "are", "was", "were", "will", "would", "can", "could", "should",
    "must", "may", "its", "has", "have", "had", "been", "being", "does", "did",
    "doing", "done", "you", "your", "our", "their", "them", "they", "there",
    "here", "when", "then", "than", "also", "only", "just", "such", "via",
    "per", "out", "off", "over", "under", "after", "before", "while", "about",
    "across", "upon", "between", "any", "all", "each", "both", "same", "other",
    "more", "most", "some", "very", "how", "why", "what", "which", "who",
    "whom", "whose",
})

_LATIN_RE = re.compile(r"[a-z0-9]+")
# Kana + CJK ideographs (incl. the CJK extension-A and half-width katakana
# blocks) — the scripts that carry no whitespace word boundaries.
_CJK_RE = re.compile(
    "[぀-ヿ㐀-䶿一-鿿ｦ-ﾟ]+"
)
_HIRAGANA_RE = re.compile("^[぀-ゟ]+$")


def significant_tokens(text: str) -> set[str]:
    """Content tokens of `text`, for overlap scoring.

    Latin script tokenises on word boundaries; CJK has none, so contiguous CJK
    runs contribute character **bigrams** instead — the standard cheap stand-in
    for a morphological analyser, and the reason a Japanese-titled issue is
    gated at all rather than silently exempt.

    All-hiragana bigrams are dropped. In Japanese those are overwhelmingly
    grammatical (`する`, `にす`, `である`), and keeping them let two completely
    unrelated titles share enough tokens to clear the threshold on inflection
    alone — the CJK equivalent of scoring English on "the" and "of".
    """
    lowered = (text or "").lower()
    tokens = {
        t for t in _LATIN_RE.findall(lowered)
        if len(t) >= _MIN_TOKEN_LEN and t not in _STOPWORDS
    }
    for run in _CJK_RE.findall(lowered):
        for i in range(len(run) - 1):
            bigram = run[i:i + 2]
            if not _HIRAGANA_RE.match(bigram):
                tokens.add(bigram)
    return tokens


# The denominator cap. Coverage is scored against at most this many title
# tokens so a verbose title (this repo's own #92 title is 15 tokens) cannot make
# a faithful one-line restatement mathematically unable to pass. With the
# threshold below it works out to "at least two title tokens must survive into
# the echo" for any title of three tokens or more.
_COVERAGE_CAP = 6
_MATCH_THRESHOLD = 0.25


def task_echo_matches(
    echo: str, brief: IssueBrief, *, threshold: float = _MATCH_THRESHOLD
) -> bool:
    """Whether `echo` plausibly restates `brief`'s issue.

    Scores what fraction of the issue **title**'s content tokens survive into
    the model's one-line restatement. The title is the reference (not the body):
    a long body's vocabulary is broad enough that almost any plausible-sounding
    echo overlaps it somewhere, which is exactly the false pass this gate exists
    to prevent.

    Calibrated to catch *substitution* — the #92 failure, where the echo and the
    issue share essentially no vocabulary — not subtle scope drift. It is a
    cheap guard in front of a 30-minute phase, not a specification checker.

    Fails **open** when the title yields no scorable tokens (e.g. a title that is
    all short words or emoji): a gate that cannot assess a run must not halt it.
    """
    title_tokens = significant_tokens(brief.title)
    if not title_tokens:
        return True
    overlap = len(title_tokens & significant_tokens(echo))
    return overlap / min(len(title_tokens), _COVERAGE_CAP) >= threshold
