# kagura-engineer

[![PyPI](https://img.shields.io/pypi/v/kagura-engineer.svg)](https://pypi.org/project/kagura-engineer/)
[![Python](https://img.shields.io/pypi/pyversions/kagura-engineer.svg)](https://pypi.org/project/kagura-engineer/)
[![License](https://img.shields.io/pypi/l/kagura-engineer.svg)](https://github.com/kagura-ai/kagura-engineer/blob/main/LICENSE)

An autonomous, memory-grounded coding harness. It drives a GitHub issue to a
pull request, and reviews pull requests.

The actor runs through [`kagura-brain`](https://github.com/kagura-ai/kagura-brain)
using Claude Code (the default) or Codex. Grounding comes from
[Kagura Memory Cloud](https://github.com/kagura-ai/memory-cloud) or an offline
SQLite store, so each run starts with what previous runs learned.

This is a `0.x` project: minor releases may include breaking changes. Check the
[changelog](https://github.com/kagura-ai/kagura-engineer/blob/main/CHANGELOG.md)
before upgrading.

## Install

Requires **Python ≥ 3.11**.

```bash
uv tool install kagura-engineer     # uv can also fetch a suitable Python
pipx install kagura-engineer
pip install kagura-engineer
```

The standalone `review` command invokes
[`kagura-code-reviewer`](https://github.com/kagura-ai/kagura-code-reviewer).
Install both together, or `review` degrades to a clean FAIL gate:

```bash
uv tool install "kagura-engineer[review]"
```

## Quick start

```bash
kagura-engineer init        # scaffold a git-ignored repo.yaml
kagura-engineer doctor      # check the dependency chain
kagura-engineer setup       # install what's missing, bootstrap auth
kagura-engineer run 42      # drive issue #42 to a pull request
```

`run` verifies the environment, recalls relevant memory, isolates a git
worktree, drives `start → implement → ship` with a gate between each phase, and
opens a pull request — checkpointing as it goes, so a blocked run resumes by
re-running the same command.

Beyond Python, a run expects `git`, `gh`, the brain CLI (`claude` or `codex`),
and the `gh-issue-driven` plugin; `ollama` is used by the reviewer.
`kagura-engineer doctor` reports exactly which of these are missing, and
`setup` installs what it can.

## Commands

| Command | What it does |
|---|---|
| `init` | Scaffold a commented, git-ignored `repo.yaml` |
| `doctor` | Check the dependency chain; `--exec-probe` also live-tests headless permissions |
| `setup` | Install missing dependencies and bootstrap authentication |
| `run <issue>` | Drive one GitHub issue to a pull request |
| `review [ref]` | Review a branch or PR and gate on the verdict; `--fix` runs the auto-fix loop |
| `eval <issues…>` | A/B the memory-grounded loop against an ungrounded control arm |

Exit codes are consistent across the operational commands: `0` success, `1` hard
failure, `2` blocked or resumable. Full behaviour, flags, and exit codes are in
the [command reference](https://github.com/kagura-ai/kagura-engineer/blob/main/docs/commands.md).

## Configuration

`kagura-engineer init` writes a commented `repo.yaml`. A minimal offline config
is two lines:

```yaml
profile: dev
memory_backend: local
```

A Cloud-backed config adds the workspace, context, and agent identity. Every
field is documented in the
[configuration reference](https://github.com/kagura-ai/kagura-engineer/blob/main/docs/configuration.md).

Headless runs need Claude Code permissions granted up front — an unattended
`claude -p` has no human to answer a permission prompt, so a missing grant
shows up as a red-halted run. See
[headless permissions](https://github.com/kagura-ai/kagura-engineer/blob/main/docs/headless-permissions.md),
and verify with `kagura-engineer doctor --exec-probe`.

## Documentation

| Page | Contents |
|---|---|
| [Commands](https://github.com/kagura-ai/kagura-engineer/blob/main/docs/commands.md) | Every command, flag, and exit code |
| [Configuration](https://github.com/kagura-ai/kagura-engineer/blob/main/docs/configuration.md) | `repo.yaml` fields, memory backends, task fidelity, brain auth |
| [Headless permissions](https://github.com/kagura-ai/kagura-engineer/blob/main/docs/headless-permissions.md) | Allowlist and workspace trust for unattended runs |
| [Development](https://github.com/kagura-ai/kagura-engineer/blob/main/docs/development.md) | Layout, tests, release process |
| [Changelog](https://github.com/kagura-ai/kagura-engineer/blob/main/CHANGELOG.md) | Shipped and breaking changes by release |

`kagura-engineer <command> --help` is authoritative for the version you have
installed.

## As a Claude Code plugin

The repository also ships a thin Claude Code skill-plugin wrapper. Its skills
shell out to the installed CLI; the harness logic stays in the Python package.
Install the CLI first, then add this repository as a marketplace source. The
plugin is also referenced by the public
[`kagura-plugins`](https://github.com/kagura-ai/kagura-plugins) marketplace.

## Related repositories

| Repo | Role |
|---|---|
| [`kagura-brain`](https://github.com/kagura-ai/kagura-brain) | Headless launcher seam for the Claude/Codex actor |
| [`memory-cloud`](https://github.com/kagura-ai/memory-cloud) | Persistent memory service and MCP server |
| [`kagura-memory-python-sdk`](https://github.com/kagura-ai/kagura-memory-python-sdk) | Memory SDK used by the Cloud client and MCP setup |
| [`kagura-code-reviewer`](https://github.com/kagura-ai/kagura-code-reviewer) | Standalone reviewer invoked by `review` |
| [`kagura-plugins`](https://github.com/kagura-ai/kagura-plugins) | Public Claude Code plugin marketplace |

## License

[Apache License 2.0](https://github.com/kagura-ai/kagura-engineer/blob/main/LICENSE)
— © 2026 Kagura AI. See
[NOTICE](https://github.com/kagura-ai/kagura-engineer/blob/main/NOTICE) for
attribution.
