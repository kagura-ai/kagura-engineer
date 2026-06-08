---
name: goal
description: Use to drive every open issue in a GitHub milestone to a pull request with the kagura-engineer harness — shells out to `kagura-engineer goal <milestone>`, running each issue through the run loop. HARNESS — high cost; it mutates the repo and creates multiple PRs. Confirm with the user before launching.
---

# kagura-engineer: goal

Thin wrapper around the `kagura-engineer goal` CLI verb. It enumerates the open issues
in a milestone and runs each through the same loop as `kagura-engineer:run`, halting at
the first blocked/failed issue. It reimplements nothing — the orchestration lives in the
CLI; this skill discovers config, gates on `doctor`, shells out, and surfaces results.

> ⚠️ **This is the highest-cost Harness in the set.** Before launching, confirm the user
> understands:
> - **Repo mutation × N** — a worktree, commits, and a PR per issue in the milestone.
> - **PR creation × N** — one pull request per shipped issue.
> - **Cost** — `run`'s `claude -p` budget multiplied across every issue.
> - **HITL** — halts at the first `blocked` issue and waits for a human decision;
>   already-shipped issues are skipped (idempotent).

**Announce:** "Using the kagura-engineer:goal skill — this is a Harness that may create multiple PRs."

## Steps

1. **Validate the milestone argument.** The CLI takes a milestone *title* (string).
   Treat it as data: validate against a **first-char-anchored** allow-list
   (`^[A-Za-z0-9][A-Za-z0-9 ._/-]{0,99}$`) so a leading `-` cannot be parsed as an
   option, and pass it after a `--` end-of-options separator, quoted (step 5). Do not
   pass unsanitized text.

2. **Discover config.** `repo.yaml` in CWD by default; pass `--config <path>` otherwise.

3. **Precondition — run doctor:**

   ```bash
   kagura-engineer doctor --json
   ```

   If `overall == "fail"` (exit 1), **stop** and hand off to `kagura-engineer:setup`.
   A blocked environment would halt the milestone at the very first issue.

4. **Confirm with the user** (the Harness warning above) unless they have opted into an
   unattended run.

5. **Run the milestone:**

   ```bash
   kagura-engineer goal --json -- "<milestone>"
   ```

   The `--` separator ends option parsing, so a milestone that survives validation but
   begins with `-` is still treated as a positional, never a flag. Add `--unattended` to
   suppress interactive gates, `--no-remember` to skip savepoints (before the `--`).

6. **Interpret the status.** `ok` (exit 0) → all issues shipped (`completed == total`).
   `blocked` (exit 2) → halted at an issue; show `resume_hint` and the per-issue table.
   `fail` (exit 1) → hard error (e.g. `gh` failed).

7. **Surface & hand off.** Print the per-issue table (issue # / status / PR URL). On a
   halt, surface the `resume_hint`; the user can address the blocking issue and re-run
   `goal` — shipped issues are no-ops, so it resumes from where it stopped.
