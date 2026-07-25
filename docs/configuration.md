# Configuration reference

Every operational command reads a per-checkout `repo.yaml`. Create one with
`kagura-engineer init` — it writes a commented template and adds the file to
`.gitignore`, because a populated config carries your workspace and context
identifiers. `setup` scaffolds the same file automatically when it is missing.

Point any command at another path with `--config` / `-c`.

Unknown keys are rejected at load time, at every nesting level, so a typo fails
loudly instead of being silently ignored. `run`, `review`, and `eval` require a
valid config and exit `2` on a configuration error. `doctor` and `setup` are
deliberately lenient so they can diagnose or bootstrap a fresh checkout.

## Full example

```yaml
profile: dev                                      # free-form execution-profile label

brain_backend: claude                             # claude | codex (default: claude)
# brain_endpoint: https://gateway.example         # optional URL or supported alias
# enable_codex_mcp: false                         # opt in to Codex in-task MCP wiring

memory_backend: cloud                             # cloud | local (default: cloud)
memory_cloud_url: https://memory.kagura-ai.com
workspace_id: ws_xxxxxxxx
context_id: 00000000-0000-0000-0000-000000000000
agent_id: 00000000-0000-0000-0000-000000000000    # registered once; see below
memory_failover: true                             # buffer failed critical writes locally
# memory_mcp_config: path/to/.mcp.json            # optional; <repo>/.mcp.json is auto-discovered

local_memory_path: .kagura/memory.db              # used only with memory_backend: local
ollama_url: http://localhost:11434

task_echo: gate                                   # gate | warn | off (default: gate)

review:
  models: [qwen2.5-coder:7b]                      # Ollama reviewer models
  max_loops: 3
  code_review: auto                               # auto | always | never
  effort: medium                                  # low | medium | high
```

## Fields

| Field | Default | Meaning |
|---|---|---|
| `profile` | *(required)* | Free-form label for this checkout, echoed in the run report |
| `brain_backend` | `claude` | Which CLI agent acts — `claude` or `codex` |
| `brain_endpoint` | `""` | Optional gateway URL or alias. Never a secret; pair it with `KAGURA_BRAIN_API_KEY` |
| `enable_codex_mcp` | `false` | Forward the MCP config to Codex (off by default as harness policy) |
| `memory_backend` | `cloud` | `cloud` (Kagura Memory Cloud) or `local` (offline SQLite) |
| `memory_cloud_url` | `""` | Required when `memory_backend: cloud` |
| `workspace_id` | `""` | Required when `memory_backend: cloud` |
| `context_id` | `""` | Required when `memory_backend: cloud` |
| `agent_id` | `""` | Agent Registry identity used by the one-call bootstrap; `run` requires it |
| `memory_failover` | `true` | Buffer failed critical writes to a local WAL and replay them next run |
| `memory_mcp_config` | *(auto)* | Override the auto-discovered `<repo>/.mcp.json` |
| `local_memory_path` | `.kagura/memory.db` | SQLite path, used only with `memory_backend: local` |
| `ollama_url` | `http://localhost:11434` | Daemon used by the standalone reviewer |
| `task_echo` | `gate` | Task-fidelity policy — see below |
| `review.models` | `[]` | Ollama models the standalone reviewer runs |
| `review.max_loops` | `3` | Maximum `review --fix` iterations |
| `review.code_review` | `auto` | Whether the actor runs an in-phase `/code-review` |
| `review.effort` | `medium` | Effort hint passed to that review |

## Memory

With `memory_backend: cloud`, `memory_cloud_url`, `workspace_id`, and
`context_id` are all required, and `run` additionally requires a registered
`agent_id`. Authentication resolves in this order:

1. `KAGURA_API_KEY` — suitable for CI and other non-interactive environments.
2. The OAuth profile written by `kagura auth login`.

With `memory_backend: local`, the Cloud fields and credentials may all be
omitted. The local backend uses SQLite with keyword-overlap recall; it does not
reproduce the Cloud service's graph and consolidation behaviour.

With Cloud failover enabled, critical writes that fail during an outage are
buffered in a local write-ahead log and replayed on a later run. Cloud remains
the source of truth.

### Agent bootstrap identity

Cloud-backed runs open with one fail-soft `get_agent_bootstrap` call that
returns the context guide, pinned and recalled memories, upcoming time memories,
and resumable state, with agent/session audit correlation. This path needs
Memory Cloud v0.49.0 or newer and a one-time Agent Registry entry.

Register the harness and bind it to this repository's context once, using an
owner/admin credential. The run both reads and writes that context, so the
binding must allow both:

```python
import asyncio
from kagura_memory import KaguraClient

CONTEXT_ID = "00000000-0000-0000-0000-000000000000"

async def main():
    async with KaguraClient() as client:
        agent = await client.register_agent(
            "kagura-engineer",
            description="Issue-to-PR engineering harness",
        )
        await client.bind_agent_context(
            agent.agent_id,
            CONTEXT_ID,
            can_read=True,
            write_policy="direct",
            is_default=True,
        )
        print(agent.agent_id)

asyncio.run(main())
```

Copy the printed UUID into `repo.yaml` as `agent_id`, then run
`kagura-engineer doctor`. Registration is intentionally manual: it is a
privileged, workspace-scoped operation, and the agent name must be unique.

### In-task memory (MCP)

For the Cloud backend, `setup` generates `<repo>/.mcp.json` so the actor can
recall *during* a task, not only from the injected prompt. Claude receives that
MCP configuration automatically. Codex keeps in-task MCP disabled by default;
set `enable_codex_mcp: true` to opt in. Codex has no per-call memory-tool
allow-list of the kind Claude gets, so its own approval and sandbox policy
remains the confinement boundary.

Recalled content is treated as untrusted reference material and must never be
followed as instructions.

## Task fidelity (`task_echo`)

Local and open-source brains have been observed running the whole pipeline to a
pull request while implementing a task unrelated to the assigned issue — they
skipped reading the issue and improvised from salient repository context.

Two harness-side guards address this, and they are always on:

- The harness fetches the issue itself and injects its title and body verbatim
  into every phase prompt, with an explicit instruction not to substitute other
  work. Acquisition is no longer something the model can skip.
- The `start` phase must restate the assigned task in one line. The orchestrator
  scores that restatement against the issue **before** dispatching the
  implement phase, so a substituted task is caught while it is still cheap.

`task_echo` controls only what happens on a mismatch:

| Value | Behaviour |
|---|---|
| `gate` *(default)* | Halt with `BLOCKED` before the implement phase runs; the run stays resumable |
| `warn` | Report the mismatch on the progress stream and continue |
| `off` | Skip the check |

Both guards fail open. If the issue cannot be read (for example `gh` is missing
or unauthenticated), the run proceeds without them rather than failing. A
dropped `KAGURA_TASK` marker counts as *unverifiable*, not *mismatched*, so a
brain that emits markers unreliably is never halted on absence alone.

Because YAML 1.1 resolves a bare `off` to a boolean, `task_echo: off` is
accepted unquoted.

## Brain authentication

- `brain_backend: claude` with no custom endpoint — authenticate the Claude Code
  CLI normally (`claude` / `claude login`).
- `brain_backend: codex` with no custom endpoint — install and authenticate the
  Codex CLI normally.
- A custom `brain_endpoint` must be paired with `KAGURA_BRAIN_API_KEY`.
  Configure both or neither; the key is never stored in `repo.yaml`.

The standalone reviewer uses its configured Ollama models. `review` needs a
brain subprocess only when `--fix` is enabled.

Headless runs additionally need Claude Code permissions granted up front — see
[headless-permissions.md](https://github.com/kagura-ai/kagura-engineer/blob/main/docs/headless-permissions.md).
