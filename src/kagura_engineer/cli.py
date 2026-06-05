from __future__ import annotations

import typer
from pydantic import ValidationError

from .config import load_config
from .doctor.registry import overall_status, run_all
from .doctor.render import print_table, to_json
from .doctor.result import Status

app = typer.Typer(help="Autonomous coding harness over Claude Code + Kagura Memory.")

_CONFIG_OPT = typer.Option("repo.yaml", "--config", "-c", help="path to repo.yaml")


@app.command()
def doctor(
    config: str = _CONFIG_OPT, json_out: bool = typer.Option(False, "--json")
) -> None:
    """Check the dependency chain."""
    try:
        cfg = load_config(config)
    except (FileNotFoundError, ValidationError) as exc:
        typer.echo(f"doctor: invalid config '{config}': {exc}", err=True)
        raise typer.Exit(code=2)
    results = run_all(cfg)
    if json_out:
        typer.echo(to_json(results))
    else:
        print_table(results)
    if overall_status(results) is Status.FAIL:
        raise typer.Exit(code=1)


@app.command()
def setup(config: str = _CONFIG_OPT) -> None:
    """Resolve dependencies (Plan 2)."""
    typer.echo("setup: not implemented yet (Plan 2)")
    raise typer.Exit(code=2)


@app.command()
def run(config: str = _CONFIG_OPT) -> None:
    """Run the idea-mode pipeline (Plan 3+)."""
    typer.echo("run: not implemented yet (Plan 3)")
    raise typer.Exit(code=2)


if __name__ == "__main__":
    app()
