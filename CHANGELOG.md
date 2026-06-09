# Changelog

All notable changes to **kagura-engineer** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While the project is in `0.x`, minor versions may carry breaking changes.

## [Unreleased]

### Added

- `init` command (#35): scaffolds a commented `repo.yaml` template (never
  overwrites an existing one) and idempotently adds `repo.yaml` to `.gitignore`,
  so a fresh checkout starts configured and the workspace/context IDs stay out
  of git by default. `kagura-engineer init [--dir <path>]`.

### Changed

- `setup` (memory-mcp step) now adds the generated `.mcp.json` to `.gitignore`
  **before** writing it — its static-token form bakes in a bearer key, so the
  ignore rule is established first (fail-secure: a write failure can never leave
  an un-ignored secret on disk). Reuses the new `init` scaffold helper rather
  than duplicating the gitignore logic (#35).
- `config`: a missing `repo.yaml` now points the user at `kagura-engineer init`
  instead of only reporting the absence (#35).

## [0.2.1] — 2026-06-09

### Fixed

- `doctor` / `setup`: the Memory Cloud `/health` probe now sends a
  `User-Agent` (`kagura-engineer/<version>`). Cloudflare blocks the stdlib
  default `Python-urllib/x` signature with HTTP 403 (CF error 1010), which made
  a perfectly healthy Memory Cloud host look unreachable.
- `doctor`: a Claude model name (`haiku` / `sonnet` / `opus` / `claude-*`) in
  `review.models` no longer suggests the impossible `ollama pull <name>` — it
  explains that `review.models` lists **Ollama** model names for the reviewer.

### Changed

- Skill wrappers now carry usage help: `kagura-engineer:run` and `:goal` print
  a usage block (with an example) when invoked without their required argument
  instead of guessing; `:review` / `:doctor` / `:setup` show a one-line usage.

## [0.2.0] — 2026-06-09

### Added

- **Claude Code skill-plugin wrapper** (`.claude-plugin/` + `skills/`) — `kagura-engineer`
  is now installable and discoverable as a plugin. Five thin skills
  (`kagura-engineer:doctor` / `:setup` / `:run` / `:review` / `:goal`) shell out to the
  CLI; referenceable by the `kagura-plugins` marketplace as a Tier-2 Harness. (#28, #30)
- **Failover memory** — Cloud-primary write durability backed by a local WAL, so a
  transient Memory Cloud outage no longer drops savepoints. (#27)
- **Incremental phase progress** streamed to stdout during `run` (suppressed under
  `--json`). (#12)
- `doctor` / `setup` now guide the Memory Cloud credential, with the README reconciled. (#6)

### Changed

- Aligned the feedback-weight contract across the cloud and local memory backends. (#21)
- Dropped the stale README "Status" table and added a "Releasing" note documenting the
  version single-source-of-truth (`__init__.py`, mirrored by the plugin manifests). (#31)
- `repo.yaml` is gitignored as per-checkout config. (#7)

### Fixed

- Added a dedicated `implement` phase between `start` and `ship`. (#9)
- Bridged the async kagura-memory SDK from the sync `MemoryClient`, and close the client
  so cloud `run`/`goal` exit cleanly. (#1, #14)
- A green `ship` that produced no PR URL now fails instead of reporting success. (#18)
- Made the native `## Verdict:` fallback phase-aware, used when the `KAGURA_VERDICT=`
  marker is absent. (#3)
- Mapped cloud feedback weight to `helpful` so reinforcement works. (#16)
- Surfaced headless `claude` stdout on failure with an `ANTHROPIC_API_KEY` hint, tightened
  the auth-failure regex, and documented `--json` progress suppression. (#19)

### Internal

- Added a skill↔CLI verb-drift guard test so a new/renamed CLI verb without a matching
  skill fails CI. (#31)

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

[Unreleased]: https://github.com/kagura-ai/kagura-engineer/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/kagura-ai/kagura-engineer/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/kagura-ai/kagura-engineer/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/kagura-ai/kagura-engineer/releases/tag/v0.1.0
