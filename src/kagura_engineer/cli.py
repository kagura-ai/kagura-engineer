from __future__ import annotations

import typer

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
    """依存チェーンを検査する。"""
    cfg = load_config(config)
    results = run_all(cfg)
    if json_out:
        typer.echo(to_json(results))
    else:
        print_table(results)
    if overall_status(results) is Status.FAIL:
        raise typer.Exit(code=1)


@app.command()
def setup(config: str = _CONFIG_OPT) -> None:
    """依存を解消する(Plan 2)。"""
    typer.echo("setup: not implemented yet (Plan 2)")
    raise typer.Exit(code=2)


@app.command()
def run(config: str = _CONFIG_OPT) -> None:
    """idea-mode パイプラインを回す(Plan 3+)。"""
    typer.echo("run: not implemented yet (Plan 3)")
    raise typer.Exit(code=2)


if __name__ == "__main__":
    app()
