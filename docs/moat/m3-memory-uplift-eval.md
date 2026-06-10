# Moat lever M3 — measuring memory-grounded uplift (A/B)

> Issue [#57](https://github.com/kagura-ai/kagura-engineer/issues/57). From the
> 2026-06-10 moat strategy review. **Depth over breadth**: one decisive,
> reproducible measurement, not a broad feature.

## The claim under test

kagura-engineer is both a consumer of Kagura Memory Cloud and its sharpest proof
point. The moat claim —

> *"a memory-grounded coding agent measurably produces better PRs because it
> remembers past work"*

— was, until this harness, **asserted but not measured**. M3 turns it into a
number you can publish (or honestly retract).

## Design — same issues, one variable

The `kagura-engineer eval` command runs the **same fixed issue set** through two
arms that are byte-for-byte identical *except* for one variable: whether memory
grounding is injected.

| Arm | What runs | Grounding |
|-----|-----------|-----------|
| **A — grounded** | the normal `run` loop | `load_pinned` + `recall` + graph-expanded (`explore`) memory injected into every phase prompt |
| **B — control** | the *identical* loop | none — `run_idea(ground=False)` skips pinned/recall/explore entirely |

The single switch is `run_idea`'s `ground` parameter (see
`src/kagura_engineer/run/__init__.py`). Everything else — guard, worktree, the
start/implement/ship phases, the gate, resume state — is the same code path. The
resume marker (`get_state`/`set_state`) is deliberately *not* disabled in the
control arm: it is part of the loop's mechanics, not grounding, so keeping it
keeps the arms identical apart from the one variable under test.

## Signals — objective, already in the pipeline

The harness compares the arms on signals the pipeline already produces, so the
measurement adds no new subjective judgement:

| Signal | Source | Good direction |
|--------|--------|----------------|
| **PR-reached rate** | `RunReport` (OK status + a real PR URL — issue #18 guarantees the link) | higher |
| **gate-verdict rate** (green / yellow / red / unknown / fail per issue) | the HITL gate verdict on each phase | more green, fewer red |
| **mean review findings** | kagura-code-reviewer JSON envelope (`--review`) | fewer |
| **mean blocking findings** (HIGH/CRITICAL) | same envelope; same blocking set the auto-fix loop uses | fewer |
| **mean re-fix iterations** to a clean review | `ReviewLoopReport.fixes_attempted` (`--review`) | fewer |

Each `(issue, arm)` run is distilled to an `ArmRun`; per-arm aggregates are an
`ArmStats`; the grounded-minus-control delta is an `Uplift` carrying a one-word
verdict: **improved / regressed / neutral / inconclusive**. The verdict folds the
per-metric "good directions" into a net signal — it is a summary, not a
significance test (see *Limitations*).

The first three signals (PR rate + gate verdicts) are produced by the run loop
alone and are cheap. The review-based signals require `--review`, which runs the
auto-review/fix loop on each arm's PR — more decisive, but it mutates each arm's
branch and roughly doubles cost again.

## How to reproduce

> ⚠️ **Cost & isolation.** `eval` runs the full `run` loop **twice per issue**
> (and, with `--review`, the fix loop on top). It mutates the repo and creates a
> PR per arm. Run it on a **pinned, disposable issue set** — not production work.
>
> Because both arms drive the *same* issue number through `run_idea`, run the two
> arms in **separate checkouts/clones** of the repo (one per arm) so their
> worktrees and branches do not collide. The harness uses `--no-remember` so the
> control arm never writes ungrounded savepoints back into memory (which would
> contaminate a later grounded run and the measurement's repeatability).

1. Pick a fixed, representative, disposable issue set (e.g. a milestone of
   closed-then-reopened issues, or a synthetic fixture milestone). Record the
   exact issue numbers — they are part of the experiment's identity.
2. Ensure the environment is healthy:
   ```bash
   kagura-engineer doctor --json
   ```
3. Run the A/B (run-only signals):
   ```bash
   kagura-engineer eval --json -- 12 14 19 23 > m3-run.json
   ```
   Or add the review-based signals (slower, mutates branches):
   ```bash
   kagura-engineer eval --review --json -- 12 14 19 23 > m3-review.json
   ```
4. Read the `uplift.verdict` and the per-arm `grounded` / `control` blocks. Keep
   the raw JSON — it is the reproducible artifact.

## Interpreting the table

```
✅ memory-grounded uplift: improved over 4 issue(s)
 signal                  grounded   control   Δ (grounded−control)
 PR-reached rate            100%       50%             50%
 green-gate rate            100%        0%            100%
 mean review findings       1.50       4.25          -2.75
 mean blocking findings     0.25       1.50          -1.25
 mean re-fix iterations     0.50       2.00          -1.50
```

A positive PR/green delta and a *negative* findings/blocking/fix delta both mean
"grounding helped". `inconclusive` means there were no comparable signals (e.g.
zero issues, or `--review` omitted so the review rows are blank).

## Result

**Status: harness shipped; the measurement is run per issue-set by the operator.**

The decisive A/B number is a property of *which issues you run* and *the state of
memory at the time* — it is not a constant baked into the repo, so it is recorded
per run rather than hard-coded here. Publish the populated table from a real run
on the pinned set below.

| Run date | Issue set | Arms | Verdict | PR-rate Δ | green-rate Δ | findings Δ | notes |
|----------|-----------|------|---------|-----------|--------------|------------|-------|
| _TBD_    | _e.g. 12 14 19 23_ | grounded vs control | _TBD_ | _TBD_ | _TBD_ | _TBD_ | publish if positive; **record honestly if not** |

> Honesty clause (from the issue): publish if positive — this is the demand-side
> moat evidence. **Record honestly if not.** A negative or neutral result is
> itself a finding: it tells us *where* grounding does and does not move PR
> quality, which is more valuable than an unfalsifiable assertion.

## Limitations & honest caveats

- **Not a significance test.** The verdict is a net-direction summary over a small
  issue set. For a publishable claim, run a large enough set and report
  per-issue variance, not just the mean delta.
- **Arm isolation is operational.** The harness drives the same issue number
  through both arms; isolating their branches/worktrees (separate checkouts) is
  the operator's responsibility — see *How to reproduce*. A future improvement
  could automate per-arm worktree/branch suffixing.
- **Memory state is a hidden variable.** The grounded arm's quality depends on
  what memory actually contains. A cold/empty context will show little uplift;
  that is a true signal about onboarding, not a harness bug.
- **`run_idea` is the unit of execution.** The eval measures end-to-end PR
  outcomes, not intermediate model behaviour. It answers "did grounding produce a
  better PR?", not "why".

## Where the code lives

- `src/kagura_engineer/eval/` — the A/B harness:
  - `__init__.py` — `run_ab_eval(issues, run_fn, review_fn=…)` orchestrator
    (execution injected, so the comparison is unit-tested with fakes).
  - `metrics.py` — pure signal extraction from a `RunReport` (+ optional review).
  - `result.py` — `ArmRun` / `ArmStats` / `Uplift` / `EvalReport`.
  - `render.py` — the A/B table + JSON artifact.
- `src/kagura_engineer/run/__init__.py` — the `ground` switch on `run_idea`.
- `skills/eval/SKILL.md` — the thin Claude Code wrapper.
- Tests: `tests/eval/` and the `ground`-toggle tests in
  `tests/run/test_orchestrator.py`.
