# Command reference

`kagura-engineer <command> --help` is authoritative for the version you have
installed. This page covers behaviour and exit codes.

All operational commands accept `--config` / `-c` to point at a `repo.yaml`
other than the default in the working directory.

---

## `init`

Creates a commented `repo.yaml` and adds it to `.gitignore`. Idempotent; never
overwrites an existing config.

```bash
kagura-engineer init
kagura-engineer init --dir path/to/repo
```

---

## `doctor`

Checks the dependency chain and prints a table or a JSON report. Each check is
isolated, so one failure never aborts the remaining checks.

| Check | Verifies |
|---|---|
| `git` | Git is available and the current directory is a work tree |
| selected brain CLI | `claude` or `codex`, according to `brain_backend` |
| `gh` | GitHub CLI is installed and authenticated |
| `ollama` | The daemon is reachable and the configured reviewer models are present |
| `haiku` | An Anthropic auth source resolves for the Haiku-dependent lane |
| `memory-cloud` / `memory-local` | The selected memory backend is usable |
| `memory-cloud-version` / `memory-agent` | Cloud supports bootstrap, and the configured agent/context binding resolves |
| `memory-mcp` / `memory-context` | Cloud MCP config and live context are valid |
| `gh-issue-driven` | The workflow plugin used by `run` is installed |
| `headless-exec` *(opt-in)* | `--exec-probe` verifies commands and file edits in an ephemeral run worktree |

```bash
kagura-engineer doctor
kagura-engineer doctor --json
kagura-engineer doctor -c path/to/repo.yaml
kagura-engineer doctor --exec-probe   # + live headless-permissions probe (spends tokens, ~30 s+)
```

**Exit codes:** `0` for OK/WARN only; `1` when any check fails. A missing or
invalid config appears as a synthetic `config` FAIL row rather than an early
exit, so a fresh checkout still gets a full diagnosis.

See [headless-permissions.md](https://github.com/kagura-ai/kagura-engineer/blob/main/docs/headless-permissions.md)
for what `--exec-probe` covers.

---

## `setup`

Installs missing dependencies and bootstraps authentication. Idempotent and safe
to re-run.

Canonical step order:

```text
git → claude-code → headless-permissions → gh → ollama → ollama-models
    → memory-cloud → memory-mcp
```

```bash
kagura-engineer setup                             # full run
kagura-engineer setup --dry-run                   # preview without side effects
kagura-engineer setup --fix gh                    # run one step
kagura-engineer setup --fix headless-permissions  # allowlist + trust guidance
kagura-engineer setup --no-input                  # never prompt
kagura-engineer setup --full                      # also install memory hooks and skills
kagura-engineer setup --json
```

Valid `--fix` targets: `git`, `claude-code`, `headless-permissions`, `gh`,
`ollama`, `ollama-models`, `memory-cloud`, `memory-mcp`.

**Exit codes:** `0` for OK/SKIPPED; `1` when any step fails; `2` when user action
is required or the config/target is invalid.

When Codex is selected, install and authenticate its CLI separately. The current
setup plan still provisions the Claude Code step used by the default backend,
and `headless-permissions` is skipped because Codex has its own sandbox and
approval model.

---

## `run`

Drives one GitHub issue through guard, memory recall, isolated worktree,
`start → implement → ship`, gate, and persistence phases. A successful run opens
or recovers a pull request and saves a resumable checkpoint.

```bash
kagura-engineer run 42
kagura-engineer run 42 --no-remember
kagura-engineer run 42 --unattended
kagura-engineer run 42 --json
```

**Exit codes:** `0` when a PR is reached; `1` for a hard failure; `2` when a
guard or gate blocks the run. Re-run the same issue to resume from persisted
state — already-shipped issues resume cheaply.

The report records the execution profile, the grounding evidence, and the actual
in-phase code-review provider/model when a review ran.

`review.code_review` controls that inner review: `auto` lets the actor decide
from diff risk, `always` forces it, and `never` disables it (in which case the
report's review record stays null, because policy guaranteed none ran).
`review.effort` supplies the effort hint.

The harness injects the assigned issue verbatim into every phase prompt and
verifies the actor's one-line restatement of the task before implementing. See
[task fidelity](https://github.com/kagura-ai/kagura-engineer/blob/main/docs/configuration.md#task-fidelity-task_echo).

---

## `review`

Invokes `kagura-code-reviewer` on a branch or PR, consumes its JSON envelope,
stores the full report in `.kagura/review.json`, and gates on the verdict.
Recalled memory is passed as untrusted, reference-only context.

```bash
kagura-engineer review                 # HEAD against main
kagura-engineer review feat/x
kagura-engineer review 42              # PR #42
kagura-engineer review --base develop
kagura-engineer review --json
kagura-engineer review --fix
```

**Exit codes:** `0` for green/yellow, or nothing to review; `1` when the review
or a fix could not run; `2` for blocking red findings.

With `--fix`, the selected brain fixes blocking findings, commits them, and
re-runs the reviewer up to `review.max_loops` times. The loop modifies the
**currently checked-out branch**, so check out the PR branch before using
`review <PR#> --fix`.

---

## `eval`

Runs a fixed issue set twice — one memory-grounded arm and one control arm — and
reports PR, gate, and optional review uplift metrics.

```bash
kagura-engineer eval 12 14 19
kagura-engineer eval 12 14 19 --review
kagura-engineer eval 12 14 19 --json
```

This launches the full run loop twice per issue and mutates isolated branches;
`--review` also runs fix loops. Use a pinned, disposable issue set.

The measurement procedure is documented in
[moat/m3-memory-uplift-eval.md](https://github.com/kagura-ai/kagura-engineer/blob/main/docs/moat/m3-memory-uplift-eval.md).
