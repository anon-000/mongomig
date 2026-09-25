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
RenameOpt = Annotated[
    list[str] | None,
    typer.Option(
        "--rename",
        help="Treat a removed+added field as a rename: COLLECTION.OLD:NEW (repeatable).",
    ),
]


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
    autogenerate: Annotated[
        bool,
        typer.Option(
            "--autogenerate", help="Generate the migration from model changes (see `diff`)."
        ),
    ] = False,
    rename: RenameOpt = None,
    json_: JsonOpt = False,
) -> None:
    """Create a new revision: empty, or --autogenerate'd from model changes."""
    _run(
        ctx,
        "revision",
        json_=json_,
        message=message,
        head=head,
        rev_id=rev_id,
        autogenerate=autogenerate,
        renames=rename or [],
    )


@app.command()
def diff(
    ctx: typer.Context,
    check: Annotated[
        bool,
        typer.Option("--check", help="Exit with code 1 if models changed without a migration."),
    ] = False,
    rename: RenameOpt = None,
    json_: JsonOpt = False,
) -> None:
    """Show what changed in your models since the last migration (offline)."""
    _run(ctx, "diff", json_=json_, check=check, renames=rename or [])


@app.command()
def baseline(
    ctx: typer.Context,
    message: Annotated[str, typer.Option("--message", "-m", help="Short description.")] = (
        "baseline"
    ),
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite a non-empty schema snapshot.")
    ] = False,
    json_: JsonOpt = False,
) -> None:
    """Adopt MongoMig on an existing database: snapshot the current models, no data changes."""
    _run(ctx, "baseline", json_=json_, message=message, force=force)


@app.command()
def heads(ctx: typer.Context, json_: JsonOpt = False) -> None:
    """Show the head revision(s)."""
    _run(ctx, "heads", json_=json_)


@app.command()
def history(ctx: typer.Context, json_: JsonOpt = False) -> None:
    """List all revisions, newest first."""
    _run(ctx, "history", json_=json_)


@app.command()
def merge(
    ctx: typer.Context,
    revisions: Annotated[
        list[str] | None,
        typer.Argument(help="Revisions to merge (default: all current heads)."),
    ] = None,
    message: Annotated[str, typer.Option("--message", "-m", help="Short description.")] = (
        "merge heads"
    ),
    rev_id: Annotated[
        str | None, typer.Option("--rev-id", help="Use this revision id instead of a random one.")
    ] = None,
    json_: JsonOpt = False,
) -> None:
    """Create a merge revision that joins several heads into one."""
    _run(ctx, "merge", json_=json_, revisions=revisions or [], message=message, rev_id=rev_id)


LockTimeoutOpt = Annotated[
    float,
    typer.Option(
        "--lock-timeout",
        min=0,
        help="Seconds to wait if another run holds the migration lock (default: fail fast).",
    ),
]
DryRunOpt = Annotated[
    bool,
    typer.Option("--dry-run", help="Show what would happen, with estimates; change nothing."),
]
StepsOpt = Annotated[
    int | None, typer.Option("--steps", "-n", min=1, help="Only this many revisions.")
]


@app.command()
def current(
    ctx: typer.Context,
    check: Annotated[
        bool,
        typer.Option("--check", help="Exit with code 1 unless the database is fully up to date."),
    ] = False,
    json_: JsonOpt = False,
) -> None:
    """Show which revisions are applied to the database, and what is pending."""
    _run(ctx, "current", json_=json_, check=check)


@app.command()
def upgrade(
    ctx: typer.Context,
    target: Annotated[
        str, typer.Argument(help="'head' (default), 'heads', or a revision id/prefix.")
    ] = "head",
    steps: StepsOpt = None,
    lock_timeout: LockTimeoutOpt = 0,
    dry_run: DryRunOpt = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Don't ask before destructive/irreversible migrations."),
    ] = False,
    json_: JsonOpt = False,
) -> None:
    """Apply pending migrations (asks first if they can delete data)."""
    _run(
        ctx,
        "upgrade",
        json_=json_,
        target=target,
        steps=steps,
        lock_timeout=lock_timeout,
        dry_run=dry_run,
        yes=yes,
    )


@app.command()
def plan(
    ctx: typer.Context,
    target: Annotated[
        str, typer.Argument(help="'head' (default), 'heads', or a revision id/prefix.")
    ] = "head",
    steps: StepsOpt = None,
    json_: JsonOpt = False,
) -> None:
    """Show pending migrations and their estimated impact and risk (changes nothing)."""
    _run(ctx, "plan", json_=json_, target=target, steps=steps)


@app.command()
def downgrade(
    ctx: typer.Context,
    target: Annotated[
        str | None,
        typer.Argument(
            help="Revision to go back to (it stays applied), or 'base'. Default: one step."
        ),
    ] = None,
    steps: StepsOpt = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Don't ask for confirmation.")] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Allow passing through irreversible migrations."),
    ] = False,
    lock_timeout: LockTimeoutOpt = 0,
    dry_run: DryRunOpt = False,
    json_: JsonOpt = False,
) -> None:
    """Revert applied migrations (asks for confirmation)."""
    _run(
        ctx,
        "downgrade",
        json_=json_,
        target=target,
        steps=steps,
        yes=yes,
        force=force,
        lock_timeout=lock_timeout,
        dry_run=dry_run,
    )


@app.command()
def stamp(
    ctx: typer.Context,
    revisions: Annotated[
        list[str], typer.Argument(help="Revision(s) to mark as current, 'heads', or 'base'.")
    ],
    lock_timeout: LockTimeoutOpt = 0,
    json_: JsonOpt = False,
) -> None:
    """Mark revisions as applied WITHOUT running them (baselines, checksum repair)."""
    _run(ctx, "stamp", json_=json_, revisions=revisions, lock_timeout=lock_timeout)


@app.command()
def inspect(
    ctx: typer.Context,
    collections: Annotated[
        list[str] | None, typer.Argument(help="Collections to inspect (default: all).")
    ] = None,
    sample_size: Annotated[
        int | None,
        typer.Option("--sample-size", min=1, help="Random sample size (default from config)."),
    ] = None,
    sample_percent: Annotated[
        float | None,
        typer.Option("--sample-percent", min=0.001, max=100, help="Sample this % of documents."),
    ] = None,
    full_scan: Annotated[
        bool, typer.Option("--full-scan", help="Read every document (slow on big collections).")
    ] = False,
    json_: JsonOpt = False,
) -> None:
    """Show the schema actually stored in MongoDB (fields, types, presence, indexes)."""
    _run(
        ctx,
        "inspect",
        json_=json_,
        collections=collections or [],
        sample_size=sample_size,
        sample_percent=sample_percent,
        full_scan=full_scan,
    )


@app.command()
def models(ctx: typer.Context, json_: JsonOpt = False) -> None:
    """Show the schema your registered models declare (as MongoMig maps them to BSON)."""
    _run(ctx, "models", json_=json_)


@app.command()
def backups(
    ctx: typer.Context,
    drop: Annotated[
        list[str] | None,
        typer.Option("--drop", help="Drop backups of this revision (or exact collection name)."),
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Don't ask for confirmation.")] = False,
    json_: JsonOpt = False,
) -> None:
    """List (or drop) backups made by unset_field/drop_collection with backup=True."""
    _run(ctx, "backups", json_=json_, drop=drop or [], yes=yes)


def main() -> None:
    app()
