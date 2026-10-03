"""`mongomig resume`: re-run failed or interrupted migrations from their checkpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mongomig.cli.context import GlobalOptions, load_config, load_graph

if TYPE_CHECKING:
    from rich.console import Console

    from mongomig.output.console import Output


def run(opts: GlobalOptions, out: Output, *, lock_timeout: float, yes: bool) -> None:
    from mongomig.cli.commands import upgrade
    from mongomig.database.client import open_database
    from mongomig.migrations.tracker import MigrationTracker, compute_state

    config = load_config(opts, out)
    graph = load_graph(config)
    with open_database(config) as db:
        records = MigrationTracker(db, config.settings.migrations.tracking_collection).records()
    failed = compute_state(graph, records).failed
    if not failed:

        def render(con: Console) -> None:
            con.print("[green]Nothing to resume: no failed or interrupted migrations.[/green]")

        out.result({"resumed": [], "direction": "upgrade", "applied": []}, render)
        return
    out.info(f"resuming {', '.join(failed)}")
    # A failed migration is pending again; upgrade continues it from its checkpoints and then
    # applies anything after it.
    upgrade.run(
        opts, out, target="head", steps=None, lock_timeout=lock_timeout, dry_run=False, yes=yes
    )
