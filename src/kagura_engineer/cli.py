from __future__ import annotations

import typer

from . import __version__
from .config import ConfigError, load_config
from .doctor.registry import overall_status, run_all
from .doctor.render import print_table, to_json
from .doctor.result import Status
from .goal import GOAL_STATUS_EXIT, run_milestone
from .goal.render import print_table as goal_print_table
from .goal.render import to_json as goal_to_json
from .run import STATUS_EXIT, run_idea
from .run.render import print_table as run_print_table
from .run.render import to_json as run_to_json
from .review import REVIEW_STATUS_EXIT, review_pr
from .review.loop import review_fix_loop
from .review.render import loop_to_json as review_loop_to_json
from .review.render import print_loop_table as review_print_loop_table
from .review.render import print_table as review_print_table
from .review.render import to_json as review_to_json
from .setup import STEP_NAMES, build_plan, run_plan
from .setup.render import print_table as setup_print_table
from .setup.render import to_json as setup_to_json

app = typer.Typer(help="Autonomous coding harness over Claude Code + Kagura Memory.")

_CONFIG_OPT = typer.Option("repo.yaml", "--config", "-c", help="path to repo.yaml")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def _main(
    version: bool = typer.Option(
        False,
        "--version",
        help="Show the kagura-engineer version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """Autonomous coding harness over Claude Code + Kagura Memory."""


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
# run (Plan 3 — memory-grounded agent loop)
# ---------------------------------------------------------------------------


@app.command()
def run(
    issue: int = typer.Argument(..., help="GitHub issue number to drive to a PR"),
    config: str = _CONFIG_OPT,
    no_remember: bool = typer.Option(
        False, "--no-remember", help="skip memory persist (recall still happens)"
    ),
    unattended: bool = typer.Option(
        False, "--unattended",
        help="dial HITL down: delegated phases proceed on green/yellow without "
             "asking (red/unknown still halt)",
    ),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Drive a GitHub issue to a PR via the memory-grounded agent loop.

    Exit codes: 0 = PR reached · 1 = hard fail · 2 = blocked
    (guard / gate halt — resumable by re-running).
    """
    try:
        cfg = load_config(config)
    except ConfigError as exc:
        typer.echo(f"run: invalid config '{config}': {exc}", err=True)
        raise typer.Exit(code=2)

    report = run_idea(cfg, issue, no_remember=no_remember, unattended=unattended)

    if json_out:
        typer.echo(run_to_json(report))
    else:
        run_print_table(report)

    raise typer.Exit(code=STATUS_EXIT[report.status])


# ---------------------------------------------------------------------------
# review (Plan 4 — reviewer 連結; --fix = Plan 4b auto-review/fix loop)
# ---------------------------------------------------------------------------


@app.command()
def review(
    target: str = typer.Argument("HEAD", help="git ref, branch, or PR number to review as head"),
    base: str = typer.Option("main", "--base", help="base ref to diff against"),
    fix: bool = typer.Option(
        False, "--fix", help="auto-fix loop: on red, claude -p fixes findings and re-reviews"
    ),
    config: str = _CONFIG_OPT,
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Launch kagura-code-reviewer on a PR/branch and gate on its JSON verdict.

    With --fix, run the Plan 4b loop: on a red verdict, claude -p fixes the
    blocking findings and commits, then re-reviews — up to `review.max_loops`.

    Exit codes: 0 = green/yellow (or nothing to review) · 1 = could not
    review / a fix failed · 2 = red (blocking findings — resumable).
    """
    try:
        cfg = load_config(config)
    except ConfigError as exc:
        typer.echo(f"review: invalid config '{config}': {exc}", err=True)
        raise typer.Exit(code=2)

    if fix:
        loop_report = review_fix_loop(cfg, target, base=base)
        if json_out:
            typer.echo(review_loop_to_json(loop_report))
        else:
            review_print_loop_table(loop_report)
        raise typer.Exit(code=REVIEW_STATUS_EXIT[loop_report.status])

    report = review_pr(cfg, target, base=base)

    if json_out:
        typer.echo(review_to_json(report))
    else:
        review_print_table(report)

    raise typer.Exit(code=REVIEW_STATUS_EXIT[report.status])


# ---------------------------------------------------------------------------
# goal (drive a whole milestone to PRs — multi-issue run loop)
# ---------------------------------------------------------------------------


@app.command()
def goal(
    milestone: str = typer.Argument(..., help="GitHub milestone title to drive to PRs"),
    config: str = _CONFIG_OPT,
    no_remember: bool = typer.Option(
        False, "--no-remember", help="skip memory persist (recall still happens)"
    ),
    unattended: bool = typer.Option(
        False, "--unattended",
        help="dial HITL down across issues: proceed on green/yellow without "
             "asking (red/unknown still halt)",
    ),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Drive every open issue in a milestone to a PR via the run loop.

    Auto-continues while issues ship; halts at the first blocked/failed issue
    (resumable by re-running). Exit codes: 0 = all shipped · 1 = hard fail ·
    2 = blocked.
    """
    try:
        cfg = load_config(config)
    except ConfigError as exc:
        typer.echo(f"goal: invalid config '{config}': {exc}", err=True)
        raise typer.Exit(code=2)

    report = run_milestone(cfg, milestone, no_remember=no_remember, unattended=unattended)

    if json_out:
        typer.echo(goal_to_json(report))
    else:
        goal_print_table(report)

    raise typer.Exit(code=GOAL_STATUS_EXIT[report.status])


if __name__ == "__main__":
    app()
