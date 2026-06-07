# Contributing to kagura-engineer

Thanks for your interest in contributing.

## Development setup

Requires **Python ≥ 3.11**.

```bash
git clone git@github.com:kagura-ai/kagura-engineer.git
cd kagura-engineer
pip install -e ".[dev]"     # editable install + pytest
```

`pyproject.toml` sets `pythonpath = ["src"]`, so `import kagura_engineer`
resolves under pytest without an editable install.

## Running tests

```bash
pytest                      # full suite
pytest tests/setup -q       # a subset
```

All changes must keep the suite green. CI runs `pytest` on Python 3.11 and 3.12
for every pull request — a red CI run blocks merge.

We practice test-driven development: write a failing test first, watch it fail,
then make it pass. Bug fixes should ship with a regression test that fails
before the fix.

## Branching & commits

- Branch off `main` with a typed prefix: `feat/…`, `fix/…`, `docs/…`,
  `chore/…`, `ci/…`.
- Use [Conventional Commits](https://www.conventionalcommits.org/) for messages,
  e.g. `fix(setup): guard whitespace-only stderr`.
- Keep commits focused; prefer several small commits over one large one.

## Pull requests

1. Open a PR against `main`.
2. Ensure CI is green and the description explains the change and how it was
   verified.
3. A maintainer reviews and merges. Direct pushes to `main` are disabled.

## License of contributions

By submitting a contribution you agree it is licensed under the
[Apache License 2.0](../LICENSE), consistent with §5 of that license
(inbound = outbound). No separate CLA is required.
