# Plan 5 — `LocalMemoryClient` (offline SQLite memory backend)

**Status:** ✅ done (merged). Implemented directly with TDD; final adversarial review applied.

**Goal:** Let `run`/`review` ground themselves fully offline — no Memory Cloud SDK, no API key, no network — by adding a SQLite-backed implementation of the existing `MemoryClient` Protocol, selectable by one config switch.

## Why

The harness depends on a narrow `MemoryClient` Protocol (`run/memory.py`):
`load_pinned`, `recall`, `remember`, `get_state`, `set_state`. The only impl was
`KaguraCloudClient`, which imports the `kagura-memory` SDK — not a declared
dependency (deps are typer/rich/pydantic/pyyaml only). So out of the box the
default memory path could not actually construct a client. Plan 5 adds an
offline impl with **zero new dependencies** (stdlib `sqlite3`), making grounded
`run`/`review` work offline and in CI.

## Design

- **`src/kagura_engineer/run/local_memory.py` — `LocalMemoryClient`** (satisfies
  the Protocol; verified via `isinstance(..., MemoryClient)`):
  - One SQLite connection per instance; writes commit immediately (a fresh
    client on the same file sees prior data). Parent dir auto-created.
  - Two tables: `memories(id, context_id, summary, content, type, tags,
    importance, pinned, created_at)` and `state(context_id, key, value)`.
  - `recall` = keyword-overlap score (no embeddings offline): rank by number of
    query terms found in `summary+content`, then importance, then recency
    (rows read recency-first; stable sort keeps recency as final tie-break).
    A query with zero matches returns `[]`.
  - `remember` inserts with a `uuid4().hex` id (importance 0.5, pinned 0).
  - `load_pinned` returns rows with `pinned=1` — empty by design offline, since
    the narrow Protocol's `remember` cannot pin (pinning is a Cloud feature).
  - `get_state`/`set_state` are JSON values, upserted, context-scoped.

- **`run/memory.py` — `resolve_memory_client(cfg)` factory**: `cfg.memory_backend
  == "local"` → `LocalMemoryClient(cfg.local_memory_path)`; else
  `KaguraCloudClient.from_config(cfg)`. The three orchestrators (`run/__init__`,
  `review/__init__`, `review/loop`) call this for their default (non-injected)
  client, so the backend is one switch away.

- **`config.py`**: `memory_backend: Literal["cloud","local"] = "cloud"` and
  `local_memory_path: str = ".kagura/memory.db"`. Default stays `cloud` (no
  behavior change); `local` is opt-in. An invalid value → `ConfigError`/exit 2.

## Tests

- `tests/run/test_local_memory.py` (12): Protocol satisfaction, distinct ids,
  keyword recall, term-overlap ranking + `k` limit, empty-on-no-match, context
  scoping (memories + state), pinned-empty-default, state roundtrip/missing,
  upsert, cross-instance persistence, parent-dir creation.
- `tests/run/test_memory.py` (+2): factory picks local vs cloud.
- Existing CLI tests repatched from `KaguraCloudClient.from_config` to the
  `resolve_memory_client` seam.

## Out of scope (Plan 5+)

- Rich `explore` (Hebbian graph), `feedback` auto-tuning, Sleep consolidation —
  Cloud-only, deferred.
- Embedding-based local recall, local pinning, importance/tag query filters.
- `doctor` check for the local DB path.
