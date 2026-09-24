from __future__ import annotations

from typing import TYPE_CHECKING

from mongomig.cli.context import GlobalOptions, load_config, load_graph

if TYPE_CHECKING:
    from rich.console import Console

    from mongomig.output.console import Output


def run(opts: GlobalOptions, out: Output) -> None:
    from mongomig.database.client import create_client, get_database, ping
    from mongomig.migrations.tracker import MigrationTracker, compute_state

    config = load_config(opts, out)
    graph = load_graph(config)

    client = create_client(config)
    try:
        ping(client, config)
        db = get_database(client, config)
        # Read-only: never creates the tracking collection.
        tracker = MigrationTracker(db, config.settings.migrations.tracking_collection)
        records = tracker.records()
    finally:
        client.close()

    state = compute_state(graph, records)

    def describe(rev: str) -> dict[str, str]:
        script = graph.scripts.get(rev)
        return {"revision": rev, "message": script.message if script else ""}

    current = [describe(r) for r in state.applied_heads]
    pending = [describe(r) for r in state.pending]
    data = {
        "database": db.name,
        "environment": config.environment,
        "current": current,
        "pending": pending,
        "failed": state.failed,
        "unknown": state.unknown,
    }

    def render(con: Console) -> None:
        env = f" [dim]({config.environment})[/dim]" if config.environment else ""
        con.print(f"Database: [bold]{db.name}[/bold]{env}")
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
            con.print(f"[red]Failed:[/red]   {rev} (last run did not complete)")
        if state.unknown:
            con.print(
                f"[yellow]Unknown:[/yellow]  {', '.join(state.unknown)} "
                "— applied in the database but no revision file here. "
                "Is this code older than the database?"
            )

    out.result(data, render)
