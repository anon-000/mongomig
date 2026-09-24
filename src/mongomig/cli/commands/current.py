from __future__ import annotations

from typing import TYPE_CHECKING

from mongomig.cli.context import GlobalOptions, load_config, load_graph
from mongomig.errors import ValidationError

if TYPE_CHECKING:
    from rich.console import Console

    from mongomig.output.console import Output


def run(opts: GlobalOptions, out: Output, *, check: bool = False) -> None:
    from mongomig.database.client import open_database
    from mongomig.migrations.tracker import MigrationTracker, compute_state

    config = load_config(opts, out)
    graph = load_graph(config)

    with open_database(config) as db:
        # Read-only: never creates the tracking collection.
        tracker = MigrationTracker(db, config.settings.migrations.tracking_collection)
        records = tracker.records()
        db_name = db.name

    state = compute_state(graph, records)
    errors = {r.revision: r.error for r in records if r.error}

    def describe(rev: str) -> dict[str, str]:
        script = graph.scripts.get(rev)
        return {"revision": rev, "message": script.message if script else ""}

    current = [describe(r) for r in state.applied_heads]
    pending = [describe(r) for r in state.pending]
    data = {
        "database": db_name,
        "environment": config.environment,
        "up_to_date": not state.pending,
        "current": current,
        "pending": pending,
        "failed": state.failed,
        "modified": state.modified,
        "unknown": state.unknown,
    }

    def render(con: Console) -> None:
        from rich.markup import escape

        env = f" [dim]({config.environment})[/dim]" if config.environment else ""
        con.print(f"Database: [bold]{db_name}[/bold]{env}")
        if state.applied_heads:
            for item in current:
                con.print(f"Current:  [bold]{item['revision']}[/bold]  {item['message']}")
        else:
            con.print("Current:  [dim]<base> (no revisions applied)[/dim]")

        if state.pending:
            con.print(f"Pending:  [yellow]{len(state.pending)}[/yellow]")
            for item in pending:
                con.print(f"  - {item['revision']}  {item['message']}")
        else:
            con.print("Pending:  [green]none — up to date[/green]")

        for rev in state.failed:
            reason = f": {escape(errors[rev])}" if rev in errors else " (interrupted)"
            con.print(f"[red]Failed:[/red]   {rev}{reason}")
        for rev in state.modified:
            con.print(f"[red]Modified:[/red] {rev} — file changed after it was applied")
        if state.unknown:
            con.print(
                f"[yellow]Unknown:[/yellow]  {', '.join(state.unknown)} "
                "— applied in the database but no revision file here. "
                "Is this code older than the database?"
            )

    out.result(data, render)

    if check and (state.pending or state.failed or state.modified):
        if out.json_mode:  # the JSON above already says "up_to_date"; keep stdout one document
            import typer

            raise typer.Exit(int(ValidationError.exit_code))
        raise ValidationError(
            "Database is not up to date"
            + (f": {len(state.pending)} pending" if state.pending else "")
            + (f", {len(state.failed)} failed" if state.failed else "")
            + (f", {len(state.modified)} modified" if state.modified else "")
            + ".",
            suggestion="Run `mongomig upgrade`.",
        )
