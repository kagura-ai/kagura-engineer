# Changelog

All notable changes to **kagura-engineer** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While the project is in `0.x`, minor versions may carry breaking changes.

## [Unreleased]

## [0.1.0] — 2026-06-08

First public release. `kagura-engineer` is a `0.x` autonomous coding harness over
[Claude Code](https://claude.ai/code) and Kagura Memory — not a finished actor.

### Added

- `doctor` — diagnose the dependency chain (git, claude, gh, ollama, Memory Cloud)
  with a backend-aware memory check.
- `setup` — install missing dependencies via the platform package manager and
  bootstrap auth; idempotent and re-runnable.
- `run` — memory-grounded agent loop driving an issue toward a PR, with
  cheap-resume savepoints and grounding enriched by recall + explore neighbours.
- `review` — launch the separate `kagura-code-reviewer` console script, read its
  JSON envelope, and gate on the verdict (`red`→halt, `green`/`yellow`→proceed).
  Available as the optional `review` extra (`pip install "kagura-engineer[review]"`);
  degrades to a clean FAIL gate when the reviewer is absent.
- `review --fix` — auto review/fix loop (Plan 4b).
- `goal <milestone>` — drive a whole milestone, with an optional `--unattended` flag.
- `LocalMemoryClient` — offline SQLite memory backend (no API key required),
  with local pinning (pin/unpin), recall tag/importance filters, graph `explore`,
  and decay maintenance clamped to `[0, 1]`.
- `memory_mcp_config` — additively attach the memory MCP server to the headless
  `claude` subprocess.
- `--version` flag on the top-level CLI.
- `memory_backend: local` no longer requires the Memory Cloud fields
  (`memory_cloud_url`/`workspace_id`/`context_id`); an offline `repo.yaml` is
  just `profile` + `memory_backend: local`. They stay required for the (default)
  cloud backend.

[Unreleased]: https://github.com/kagura-ai/kagura-engineer/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/kagura-ai/kagura-engineer/releases/tag/v0.1.0
