# Development

```bash
git clone https://github.com/kagura-ai/kagura-engineer.git
cd kagura-engineer
pip install -e ".[dev]"
pytest
```

`pyproject.toml` sets `pythonpath = ["src"]`, so the test suite imports the
package without an editable install. Note the corollary: this is a src-layout
project, so a bare `python -c "import kagura_engineer"` loads the *installed*
copy, not your working tree. Verify in-repo changes with `pytest` or
`PYTHONPATH=src`.

Coverage has a floor of 90% (`fail_under` in `pyproject.toml`).

## Project layout

```text
kagura-engineer/
├── README.md
├── CHANGELOG.md
├── docs/
│   ├── README.md             # document map
│   ├── commands.md           # command reference
│   ├── configuration.md      # repo.yaml reference
│   ├── headless-permissions.md
│   ├── development.md        # this file
│   ├── moat/                 # current operational evaluation procedures
│   ├── plan/                 # historical implementation plans
│   └── superpowers/          # historical design and planning records
├── src/kagura_engineer/
│   ├── cli.py                # init / doctor / setup / run / review / eval
│   ├── config.py             # repo.yaml schema and loading
│   ├── profile.py            # resolved execution-profile reporting
│   ├── mcp.py                # in-task memory MCP policy
│   ├── _launch.py · _http.py # platform-safe process and HTTP helpers
│   ├── doctor/               # dependency checks and reporting
│   ├── setup/                # scaffolding, install, auth, MCP setup
│   ├── run/                  # actor loop, issue brief, memory, worktrees, gates, failover
│   ├── review/               # reviewer integration and auto-fix loop
│   └── eval/                 # grounded-versus-control A/B harness
└── tests/
```

## Releasing

The canonical version lives in `src/kagura_engineer/__init__.py`, read by Hatch
at build time. Three further files must move with it in the **same** commit:

| File | Why |
|---|---|
| `src/kagura_engineer/__init__.py` | Canonical `__version__`, read by Hatch |
| `.claude-plugin/plugin.json` | Claude Code plugin manifest |
| `.claude-plugin/marketplace.json` | Marketplace entry |
| `tests/test_version.py` | Asserts the literal version |

`tests/test_plugin.py` asserts the first three are equal, so any skew fails CI.

Publishing to PyPI is driven by `.github/workflows/publish.yml` on a pushed tag.

## Documentation conventions

`README.md` is also the PyPI project page. PyPI renders it standalone: relative
paths have no repository root to resolve against, and its HTML sanitizer strips
heading `id` attributes, so intra-page anchors do not resolve either. On top of
that, `docs/` is excluded from the sdist.

**Every link in `README.md` must therefore be an absolute URL.**
`tests/test_readme.py` enforces this.

Reference-depth material belongs in `docs/`, linked from the README by absolute
`https://github.com/kagura-ai/kagura-engineer/blob/main/...` URL. Files under
`plan/` and `superpowers/` are dated records of intent at the time of writing,
not current specification.
