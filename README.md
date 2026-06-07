# kagura-engineer

> Part of the Kagura Memory Cloud offering. Licensed under
> [Apache-2.0](LICENSE) — © 2026 Kagura AI.

An autonomous coding harness over [Claude Code](https://claude.ai/code) and
[Kagura Memory Cloud](https://github.com/kagura-ai/memory-cloud).

The long-term goal is a memory-backed **actor** that executes real, resumable
coding tasks (see [Roadmap](#roadmap)). What ships **today** is the bootstrap
layer that gets a machine ready to run it: a `doctor` that checks the dependency
chain and a `setup` that resolves it.

---

## Status

| Phase | Scope | State |
|---|---|---|
| **Plan 1** | `doctor` — diagnose the dependency chain | ✅ shipped |
| **Plan 2** | `setup` — install + bootstrap the environment | ✅ shipped |
| **Plan 3** | `run` — memory-grounded agent loop (issue→PR) | ✅ done |
| **Plan 4** | `review` — launch the reviewer, gate on its JSON verdict | ✅ done (v1) |
| Plan 4b+ | auto-review/fix loop, memory auto-store, worktree runs | 📋 planned |

`doctor`, `setup`, `run`, and `review` are runnable now (289 tests green).

---

## Install

Requires **Python ≥ 3.11**.

### As a tool (recommended)

Not published to public PyPI (yet) — install straight from the repository
(or, later, a private index). `uv` and `pipx` pull from git just as they would
from an index; `uv` will also fetch a suitable Python for you.

```bash
# uv (also bootstraps Python 3.11 if needed)
uv tool install git+ssh://git@github.com/kagura-ai/kagura-engineer.git

# or pipx
pipx install git+ssh://git@github.com/kagura-ai/kagura-engineer.git
```

Pin a version with a tag: `…kagura-engineer.git@v0.1.0`.

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
memory_cloud_url: https://memory.kagura-ai.com    # required
workspace_id: ws_xxxxxxxx                          # required — Memory Cloud scope
context_id: 00000000-0000-0000-0000-000000000000  # required — context within the workspace
ollama_url: http://localhost:11434                 # optional (default shown)
review:
  models: [qwen2.5-coder:7b, haiku]               # optional (default: [])
  max_loops: 3                                      # optional (default: 3)
```

`workspace_id → context_id → memory` is the Memory Cloud filter hierarchy.
A missing required field, unparseable YAML, or an unreadable file fails with a
clean error and **exit code 2**.

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
| `memory-cloud` | `memory_cloud_url` reachable (host-only; credentials never echoed) |

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
```

Exit codes: `0` green/yellow (or nothing to review) · `1` could not review
(reviewer infra error) · `2` red (blocking findings — resumable).

---

## Project layout

```
kagura-engineer/
├── README.md                  # this file
├── pyproject.toml
├── docs/plan/                 # design docs (plan-2-setup.md, …)
├── src/kagura_engineer/
│   ├── cli.py                 # typer app: doctor / setup / run
│   ├── config.py              # repo.yaml loader + Config (pydantic)
│   ├── doctor/                # Plan 1 — checks, registry, result, render
│   │   ├── checks.py
│   │   ├── registry.py        # run_all(cfg) → [CheckResult], per-check isolation
│   │   ├── result.py          # Status (OK/WARN/FAIL), CheckResult
│   │   └── render.py          # table + json
│   └── setup/                 # Plan 2 — step orchestrator
│       ├── __init__.py        # STEP_NAMES, build_plan, run_plan → SetupReport
│       ├── auth.py            # resolve_anthropic_auth (shared with doctor)
│       ├── install.py         # run_install helper + stderr_tail
│       ├── platform.py        # OS / package-manager / WSL detection
│       ├── result.py          # StepStatus, StepResult, SetupReport
│       ├── git.py · claude.py · gh.py · ollama.py · memory_cloud.py
│       └── render.py
└── tests/                     # pytest (pythonpath = src)
```

---

## Development

```bash
pip install -e ".[dev]"
pytest                         # 243 tests
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
