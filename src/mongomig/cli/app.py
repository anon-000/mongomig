"""CLI entry point.

Only the command *signatures* live here. Each implementation is in ``cli/commands/<name>.py``
and is imported when the command runs, keeping ``mongomig --help`` fast.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Annotated, Any

import typer

from mongomig._version import __version__
from mongomig.cli.context import GlobalOptions

app = typer.Typer(
    name="mongomig",
    help="Alembic-style schema evolution and migrations for MongoDB.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
    rich_markup_mode=None,
)

JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable JSON output.")]


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"mongomig {__version__}")
        raise typer.Exit()


@app.callback()
def _global(
    ctx: typer.Context,
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            "-c",
            envvar="MONGOMIG_CONFIG",
            help="Path to mongomig.yaml.",
            dir_okay=False,
        ),
    ] = None,
    env: Annotated[
        str | None,
        typer.Option(
            "--env",
            "-e",
            envvar="MONGOMIG_ENV",
            help="Environment overlay to apply (mongomig.<env>.yaml).",
        ),
    ] = None,
    json_: JsonOpt = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="More detail.")] = False,
    _version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_version_callback, is_eager=True, help="Show version and exit."
        ),
    ] = False,
) -> None:
    ctx.obj = GlobalOptions(config=config, env=env, json=json_, verbose=verbose)


def _run(ctx: typer.Context, command: str, *, json_: bool = False, **kwargs: Any) -> None:
    from mongomig.errors import MongoMigError
    from mongomig.output.console import Output

    opts: GlobalOptions = ctx.obj
    out = Output(json_mode=opts.json or json_, verbose=opts.verbose)
    module = importlib.import_module(f"mongomig.cli.commands.{command}")
    try:
        module.run(opts, out, **kwargs)
    except MongoMigError as err:
        out.error(err)
        raise typer.Exit(int(err.exit_code)) from None


@app.command()
def init(
    ctx: typer.Context,
    directory: Annotated[
        Path, typer.Argument(help="Project directory to initialise.", file_okay=False)
    ] = Path("."),
    migrations_dir: Annotated[
        str, typer.Option("--migrations-dir", help="Name of the migrations directory.")
    ] = "migrations",
    json_: JsonOpt = False,
) -> None:
    """Create mongomig.yaml and the migrations/ directory."""
    _run(ctx, "init", json_=json_, directory=directory, migrations_dir=migrations_dir)


@app.command()
def revision(
    ctx: typer.Context,
    message: Annotated[str, typer.Option("--message", "-m", help="Short description.")],
    head: Annotated[
        str | None,
        typer.Option("--head", help="Parent revision (required when there are several heads)."),
    ] = None,
    rev_id: Annotated[
        str | None, typer.Option("--rev-id", help="Use this revision id instead of a random one.")
    ] = None,
    json_: JsonOpt = False,
) -> None:
    """Create a new, empty revision file."""
    _run(ctx, "revision", json_=json_, message=message, head=head, rev_id=rev_id)


@app.command()
def heads(ctx: typer.Context, json_: JsonOpt = False) -> None:
    """Show the head revision(s)."""
    _run(ctx, "heads", json_=json_)


@app.command()
def history(ctx: typer.Context, json_: JsonOpt = False) -> None:
    """List all revisions, newest first."""
    _run(ctx, "history", json_=json_)


@app.command()
def current(ctx: typer.Context, json_: JsonOpt = False) -> None:
    """Show which revisions are applied to the database, and what is pending."""
    _run(ctx, "current", json_=json_)


def main() -> None:
    app()
