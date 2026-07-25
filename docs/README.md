# Documentation

All links here are absolute `https://` URLs, matching the root `README.md`. They
resolve identically from a checkout, from github.com, from the PyPI project
page, and from anything that mirrors these files without their directory
structure.

## Current

| Page | Contents |
|---|---|
| [README.md](https://github.com/kagura-ai/kagura-engineer/blob/main/README.md) | Overview, install, quick start — also the PyPI project page |
| [commands.md](https://github.com/kagura-ai/kagura-engineer/blob/main/docs/commands.md) | Every command, flag, and exit code |
| [configuration.md](https://github.com/kagura-ai/kagura-engineer/blob/main/docs/configuration.md) | `repo.yaml` fields, memory backends, task fidelity, brain auth |
| [headless-permissions.md](https://github.com/kagura-ai/kagura-engineer/blob/main/docs/headless-permissions.md) | Allowlist and workspace trust for unattended runs |
| [development.md](https://github.com/kagura-ai/kagura-engineer/blob/main/docs/development.md) | Layout, tests, release process, doc conventions |
| [CHANGELOG.md](https://github.com/kagura-ai/kagura-engineer/blob/main/CHANGELOG.md) | Shipped and breaking changes by release |
| [moat/m3-memory-uplift-eval.md](https://github.com/kagura-ai/kagura-engineer/blob/main/docs/moat/m3-memory-uplift-eval.md) | Operational procedure for the memory-grounding A/B evaluation |

`kagura-engineer <command> --help` is authoritative for the version you have
installed.

## Historical records

Files under `plan/` and `superpowers/` are dated design and implementation
records. They are retained for decision history and may contain
pre-implementation status, internal plan numbers, or commands that were later
changed or removed. **They are not the current user guide or a release-status
tracker.**

## Why absolute links

The root `README.md` *is* the PyPI project page. PyPI renders it standalone:
there is no repository root for a relative path to resolve against, its HTML
sanitizer strips heading `id` attributes so intra-page anchors do not resolve,
and `docs/` is excluded from the sdist entirely — so a relative link there is
broken twice over.

`tests/test_readme.py` enforces the rule for `README.md` and for these pages.
See
[development.md](https://github.com/kagura-ai/kagura-engineer/blob/main/docs/development.md).
