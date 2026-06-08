# kagura-engineer

> Part of the Kagura Memory Cloud offering. Licensed under
> [Apache-2.0](LICENSE) — © 2026 Kagura AI.

An autonomous coding harness over [Claude Code](https://claude.ai/code) and
[Kagura Memory Cloud](https://github.com/kagura-ai/memory-cloud).

The long-term goal is a memory-backed **actor** that executes real, resumable
coding tasks (see [Roadmap](#roadmap)). Shipping **today**: `doctor` and `setup`
stand up the environment, and `run` / `review` / `goal` drive GitHub issues to
PRs through a memory-grounded loop. It's an early `0.x` harness, not a finished
actor. [Memory Cloud](https://github.com/kagura-ai/memory-cloud) is the
recommended backbone (free to start), with an offline SQLite fallback for the
basic loop.

---

## Status

| Phase | Scope | State |
|---|---|---|
| **Plan 1** | `doctor` — diagnose the dependency chain | ✅ shipped |
| **Plan 2** | `setup` — install + bootstrap the environment | ✅ shipped |
| **Plan 3** | `run` — memory-grounded agent loop (issue→PR) | ✅ done |
| **Plan 4** | `review` — launch the reviewer, gate on its JSON verdict | ✅ done |
| **Plan 4b** | `review --fix` — auto-review/fix loop | ✅ done |
| **Plan 5** | `LocalMemoryClient` — offline SQLite memory backend | ✅ done |
| Plan 5+ | rich graph/feedback/Sleep, memory auto-store, worktree runs — **Memory Cloud required** | 📋 planned |

`doctor`, `setup`, `run`, `review`, and `goal` are runnable now (390 tests green).

---

## Install

Requires **Python ≥ 3.11**.

### As a tool (recommended)

Published on [PyPI](https://pypi.org/project/kagura-engineer/). `uv` will also
fetch a suitable Python for you.

```bash
# uv (also bootstraps Python 3.11 if needed)
uv tool install kagura-engineer

# or pipx
pipx install kagura-engineer

# or plain pip
pip install kagura-engineer
```

The `review` command shells out to the separate
[`kagura-code-reviewer`](https://github.com/kagura-ai/kagura-code-reviewer)
console script. Pull it in alongside the harness with the `review` extra:

```bash
uv tool install "kagura-engineer[review]"
```

(`kagura-engineer setup` can also bootstrap it later; without it, `review`
degrades to a clean FAIL gate.)

To install straight from the repository instead — e.g. an unreleased commit:

```bash
uv tool install git+ssh://git@github.com/kagura-ai/kagura-engineer.git
```

Pin a version with a tag: `pip install kagura-engineer==0.1.0`.

### For development (from a checkout)

```bash
pip install -e ".[dev]"     # editable install + pytest
```

Either way, this exposes the `kagura-engineer` CLI (entry point
`kagura_engineer.cli:app`).

---

## Configuration

Every command reads a `repo.yaml` (override with `--config / -c`):

```yaml
profile: coding                                   # required
memory_cloud_url: https://memory.kagura-ai.com    # required for cloud backend
workspace_id: ws_xxxxxxxx                          # required for cloud backend — Memory Cloud scope
context_id: 00000000-0000-0000-0000-000000000000  # required for cloud backend — context within the workspace
ollama_url: http://localhost:11434                 # optional (default shown)
memory_backend: cloud                              # optional: cloud | local (default: cloud)
local_memory_path: .kagura/memory.db               # optional (used only when backend=local)
memory_mcp_config: .mcp/kagura-memory.json         # optional: attach memory MCP to headless phases
review:
  models: [qwen2.5-coder:7b, haiku]               # optional (default: [])
  max_loops: 3                                      # optional (default: 3)
```

`workspace_id → context_id → memory` is the Memory Cloud filter hierarchy.
A missing required field, unparseable YAML, or an unreadable file fails with a
clean error and **exit code 2**. With `memory_backend: local` the three
Cloud-only fields may be omitted — an offline `repo.yaml` is just `profile` +
`memory_backend: local`.

**Memory backend.** Memory Cloud is the recommended default and is **free to
start**. Authenticate with **either** of two equivalent credentials — `run`
honours both, env-first:

- `export KAGURA_API_KEY=...` — a workspace API key. Explicit and CI-friendly.
- `kagura auth login` — installs the `kagura` CLI and writes an OAuth profile
  to `~/.kagura/credentials.json`. Used automatically when `KAGURA_API_KEY` is
  unset.

When `KAGURA_API_KEY` is set it takes precedence; otherwise the `kagura auth
login` profile is used. `doctor` and `setup` both check that one of these
resolves and guide you if neither does — a reachable host with no credential is
flagged, not silently passed. With a credential in place `run`/`review` are
grounded immediately. The richer capabilities — graph discovery, feedback
reinforcement, Sleep consolidation, memory auto-store, and worktree runs
(Plan 5+) — **require Memory Cloud**.

For offline or CI use, `memory_backend: local` switches the **basic** `run`/
`review` grounding to an offline SQLite store (`local_memory_path`, stdlib
`sqlite3` — no API key, no network). It implements the same client Protocol;
offline recall is a keyword-overlap match (no embeddings). The local backend
covers the basic grounding loop only — the Plan 5+ features stay Cloud-only.

**In-task memory MCP.** By default the harness *string-injects* recalled memory
into each headless `claude -p` prompt. Set `memory_mcp_config` to a Claude Code
MCP config (`{"mcpServers": {"kagura-memory": {…}}}`) and the run/fix phases get
the `kagura-memory` recall/remember tools attached (`--mcp-config`, additive),
so the model can recall *during* the task. The server's tools must be permitted
in your Claude settings; recalled content is treated as untrusted reference.

---

## Commands

### `kagura-engineer doctor`

Checks the dependency chain and prints a status table (or `--json`). Each check
is isolated — one failing check never aborts the rest of the run.

| Check | Verifies |
|---|---|
| `git` | `git` on PATH, inside a work tree |
| `claude-code` | `claude` on PATH + version, auth source (API key / subscription) |
| `gh` | `gh` on PATH and authenticated |
| `ollama` | daemon reachable at `ollama_url`, `review.models` present (tag-aware match) |
| `haiku` | an Anthropic auth source resolves (env key or subscription cache) |
| `memory` | backend-aware: `memory-cloud` reachable, or (when `memory_backend: local`) `memory-local` SQLite writable — host/credentials never echoed |
| `gh-issue-driven` | the `gh-issue-driven` plugin is installed (the workflow `run`/`goal` drive) |

Statuses: **OK / WARN / FAIL**.

```bash
kagura-engineer doctor
kagura-engineer doctor --json
kagura-engineer doctor -c path/to/repo.yaml
```

Exit codes: `0` all OK/WARN · `1` any FAIL · `2` config error.

### `kagura-engineer setup`

Resolves the same dependencies end-to-end: installs what's missing (via the
platform package manager) and bootstraps auth. Idempotent and re-runnable.

Steps run in canonical order:

```
git → claude-code → gh → ollama → ollama-models → memory-cloud
```

Statuses: **OK / SKIPPED / NEEDS_USER / FAIL**. Interactive actions (a login
that can't be automated) surface as `NEEDS_USER`.

```bash
kagura-engineer setup                  # full run
kagura-engineer setup --dry-run        # preview only; no side effects
kagura-engineer setup --fix gh         # run a single step
kagura-engineer setup --no-input       # never prompt; fail loudly on user-action steps
kagura-engineer setup --json
```

Exit codes (Plan 2 design doc §1.6): `0` all OK/SKIPPED · `1` any FAIL
(wins over 2) · `2` any NEEDS_USER, or a config / unknown `--fix` error.

Valid `--fix` targets: `git`, `claude-code`, `gh`, `ollama`, `ollama-models`,
`memory-cloud`.

### `kagura-engineer run`

The memory-grounded agent loop. `run <issue#>` verifies the environment,
recalls relevant memory, isolates a worktree, drives `gh-issue-driven`
start→ship via headless `claude -p` (HITL gate on red/unknown verdicts),
and opens a PR — persisting a savepoint to Memory Cloud.

```
kagura-engineer run 42                 # drive issue #42 to a PR
kagura-engineer run 42 --no-remember   # recall but don't persist
kagura-engineer run 42 --unattended    # don't pause on green/yellow (red still halts)
kagura-engineer run 42 --json
```

Exit codes: `0` PR reached · `1` hard fail · `2` blocked (guard or gate
halt — resumable by re-running).

### `kagura-engineer review`

Launches the separate [`kagura-code-reviewer`](https://github.com/kagura-ai/kagura-code-reviewer)
on a PR or branch, reads its machine-readable JSON envelope (never scrapes
Markdown), and gates on the `verdict`. Recalled memory is passed to the
reviewer as an untrusted, reference-only `--context-file`; the raw report is
written to `.kagura/review.json` so you can read the full findings. This is
v1 (review + gate) — the auto-review/fix loop is a later plan.

```
kagura-engineer review                 # review HEAD against main
kagura-engineer review feat/x          # review a branch
kagura-engineer review 42              # review PR #42 (resolved to its branch)
kagura-engineer review --base develop  # diff against a different base
kagura-engineer review --json          # machine-readable report
kagura-engineer review --fix           # auto-fix loop (Plan 4b)
```

Exit codes: `0` green/yellow (or nothing to review) · `1` could not review
(reviewer infra error) · `2` red (blocking findings — resumable).

With `--fix`, a red verdict triggers the **auto-fix loop**: `claude -p` reads
the persisted findings, fixes the blocking ones and commits, then re-reviews —
repeating up to `review.max_loops` times. The reviewer stays bounded (it only
emits findings); the actor does the edits. A review that *couldn't run* (infra
error) never triggers a fix.

`--fix` commits to (and re-reviews) the **currently checked-out branch**, so
check out the branch you want fixed before running it — for `review <PR#> --fix`
that means the PR's head branch.

### `kagura-engineer goal`

Drive a whole **milestone** to PRs: enumerate its open issues (via `gh`) and run
the `run` loop over each, in order. It auto-continues while issues ship and
halts at the first blocked/failed issue (resumable by re-running — already-shipped
issues resume cleanly).

```
kagura-engineer goal v0.3              # drive milestone "v0.3" to PRs
kagura-engineer goal v0.3 --unattended # don't pause on green/yellow (red still halts)
kagura-engineer goal v0.3 --json
```

Exit codes: `0` all issues shipped · `1` hard fail · `2` blocked (an issue's
gate halted — resolve it, then re-run).

### Headless auth (`run` / `review --fix` / `goal`)

These commands spawn headless `claude -p` subprocesses, which need a **valid
Anthropic credential** in the environment. Two options, in recommended order:

1. **A Claude Pro/Max subscription (recommended)** — run `claude` once to
   `claude login`. `run`/`goal` fan out *many* `claude -p` phases per issue, so a
   flat-rate subscription is dramatically cheaper than metered API billing for an
   autonomous loop. Caveat: heavy runs can hit subscription rate limits — if you
   drive a whole milestone unattended (CI/cron), use an API key instead.
2. **`ANTHROPIC_API_KEY`** — a metered API key. Best for unattended CI where no
   interactive `claude login` seat exists. Must be a real value (an empty string
   is treated as unset).

`doctor`'s `claude-code` check only verifies the binary launches, not that auth
works, so a bad credential surfaces as a phase that can't produce a verdict.

> **Nested-in-Claude-Code gotcha:** if you run kagura-engineer from *inside* a
> Claude Code session, the inherited `ANTHROPIC_API_KEY` is that session's
> internal token and is **invalid for a standalone `claude -p`**. Drop it so the
> child falls back to your `claude login`:
> ```
> env -u ANTHROPIC_API_KEY kagura-engineer run 42
> ```

---

## Project layout

```
kagura-engineer/
├── README.md                  # this file
├── pyproject.toml
├── docs/plan/                 # design docs (plan-2-setup.md, …)
├── src/kagura_engineer/
│   ├── cli.py                 # typer app: doctor / setup / run / review / goal
│   ├── config.py              # repo.yaml loader + Config (pydantic)
│   ├── proc.py                # shared subprocess helper
│   ├── doctor/                # Plan 1 — checks, registry, result, render
│   │   ├── checks.py
│   │   ├── registry.py        # run_all(cfg) → [CheckResult], per-check isolation
│   │   ├── result.py          # Status (OK/WARN/FAIL), CheckResult
│   │   └── render.py          # table + json
│   ├── setup/                 # Plan 2 — step orchestrator
│   │   ├── __init__.py        # STEP_NAMES, build_plan, run_plan → SetupReport
│   │   ├── auth.py            # resolve_anthropic_auth (shared with doctor)
│   │   ├── install.py         # run_install helper + stderr_tail
│   │   ├── platform.py        # OS / package-manager / WSL detection
│   │   ├── result.py          # StepStatus, StepResult, SetupReport
│   │   ├── git.py · claude.py · gh.py · ollama.py · memory_cloud.py
│   │   └── render.py
│   ├── run/                   # Plan 3 — memory-grounded agent loop
│   │   ├── memory.py          # MemoryClient Protocol + KaguraCloudClient
│   │   ├── local_memory.py    # Plan 5 — offline SQLite backend
│   │   └── gate.py · workflow.py · worktree.py · result.py · render.py
│   ├── review/                # Plan 4 — reviewer launch + verdict gate
│   │   └── reviewer.py · envelope.py · loop.py · fixer.py · context.py · …
│   └── goal/                  # milestone driver over run
│       └── issues.py · render.py · result.py
└── tests/                     # pytest (pythonpath = src)
```

---

## Development

```bash
pip install -e ".[dev]"
pytest                         # 390 tests
```

`pyproject.toml` sets `pythonpath = ["src"]`, so `import kagura_engineer`
resolves under pytest without an editable install.

---

## Roadmap

The bootstrap CLI exists to stand up the environment for the actual product: a
**memory + actor** harness. The defining capability is the combination, not the
parts — a stateless agent or a memory-less actor doesn't get there.

- **Cost-aware planning** — recall past similar tasks' real cost/failure modes
  and budget the plan accordingly.
- **Long-running task resume** — checkpoint task state to Memory Cloud; resume
  cleanly in a fresh context after the window dies.
- **Failure-mode learning** — every failure becomes a memory with a `prevents`
  edge to its fix, surfaced preemptively next time. Recurring-failure cost → 0.
- **Sub-agent dispatch with memory handoff** — children receive context as
  memory IDs, not prompt text, keeping the parent context small.

Claude is driven via the Claude Code CLI; a Pro/Max subscription is inherited
for self-hosted use, with `ANTHROPIC_API_KEY` (BYOK) as the multi-tenant
fallback. Memory Cloud is the persistent backbone, consumed as the primary MCP
server (`recall` / `remember` / `create_edge` / `explore` / …).

**Explicit non-goals:** not a chat interface for Memory Cloud, not a fine-tuned
or domain model, not a chat-ingestion source, not a memory analyzer. The job is
autonomous task execution with persistent memory — nothing more.

---

## Related repositories

| Repo | Role | Relationship |
|---|---|---|
| [`memory-cloud`](https://github.com/kagura-ai/memory-cloud) | Persistence + MCP server | **The backbone.** Primary MCP. |
| [`kagura-memory-python-sdk`](https://github.com/kagura-ai/kagura-memory-python-sdk) | Primitive SDK | Used by the memory MCP client wrapper. |
| [`kagura-memory-ai-worker`](https://github.com/kagura-ai/kagura-memory-ai-worker) | Chat ingestion | Produces memories the harness later reads. |
| [`kagura-memory-dataset-worker`](https://github.com/kagura-ai/kagura-memory-dataset-worker) | Export + fine-tune | Independent; may export harness-produced memories. |
| [`kagura-embeddings-worker`](https://github.com/kagura-ai/kagura-embeddings-worker) | Sovereign embeddings | Indirect — `recall` quality depends on the workspace's embeddings lane. |

---

## License

[Apache License 2.0](LICENSE) — © 2026 Kagura AI. See `LICENSE` for the full
text and `NOTICE` for attribution.
