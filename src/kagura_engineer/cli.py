from __future__ import annotations

import typer

from .config import ConfigError, load_config
from .doctor.registry import overall_status, run_all
from .doctor.render import print_table, to_json
from .doctor.result import Status
from .setup import STEP_NAMES, build_plan, run_plan
from .setup.render import print_table as setup_print_table
from .setup.render import to_json as setup_to_json

app = typer.Typer(help="Autonomous coding harness over Claude Code + Kagura Memory.")

_CONFIG_OPT = typer.Option("repo.yaml", "--config", "-c", help="path to repo.yaml")


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


@app.command()
def doctor(
    config: str = _CONFIG_OPT, json_out: bool = typer.Option(False, "--json")
) -> None:
    """Check the dependency chain."""
    try:
        cfg = load_config(config)
    except ConfigError as exc:
        typer.echo(f"doctor: invalid config '{config}': {exc}", err=True)
        raise typer.Exit(code=2)
    results = run_all(cfg)
    if json_out:
        typer.echo(to_json(results))
    else:
        print_table(results)
    if overall_status(results) is Status.FAIL:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------


def _check_fix_name(only: str | None, plan: list[str]) -> str | None:
    """Return an error message string if `only` is given but does not
    match any registered step, otherwise None. Kept as a small pure
    helper so tests can exercise it without spinning up typer.
    """
    if only is None:
        return None
    if not plan:
        return (
            f"setup: unknown --fix target {only!r}; "
            f"valid names: {', '.join(STEP_NAMES)}"
        )
    return None


@app.command()
def setup(
    config: str = _CONFIG_OPT,
    fix: str | None = typer.Option(
        None, "--fix", help="run only the named step (e.g. --fix git)"
    ),
    no_input: bool = typer.Option(
        False, "--no-input", help="never prompt; fail loudly on user-action steps"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="preview all steps without executing; exit 0/1/2 on feasibility"
    ),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Resolve dependencies and bootstrap a healthy dev environment.

    Exit codes (Plan 2 design doc §1.6):

        0 — all steps OK or SKIPPED
        1 — at least one step FAIL (hard error)
        2 — at least one step NEEDS_USER (interactive action required)
        2 — also used for config / unknown --fix errors
    """
    try:
        cfg = load_config(config)
    except ConfigError as exc:
        typer.echo(f"setup: invalid config '{config}': {exc}", err=True)
        raise typer.Exit(code=2)

    # Validate --fix before running anything.
    err = _check_fix_name(fix, build_plan(only=fix))
    if err is not None:
        typer.echo(err, err=True)
        raise typer.Exit(code=2)

    report = run_plan(cfg, no_input=no_input, dry_run=dry_run, only=fix)

    if json_out:
        typer.echo(setup_to_json(report))
    else:
        setup_print_table(report)

    # Exit-code policy:
    #   - 1 wins over 2: if any FAIL, the user has a hard error to
    #     fix and NEEDS_USER items are secondary
    #   - 2 only when there is NEEDS_USER but NO FAIL
    if report.failed:
        raise typer.Exit(code=1)
    if report.needs_user:
        raise typer.Exit(code=2)


# ---------------------------------------------------------------------------
# run (Plan 3 placeholder)
# ---------------------------------------------------------------------------


@app.command()
def run(config: str = _CONFIG_OPT) -> None:
    """Run the idea-mode pipeline (Plan 3+)."""
    typer.echo("run: not implemented yet (Plan 3)")
    raise typer.Exit(code=2)


if __name__ == "__main__":
    app()
