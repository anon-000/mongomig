from __future__ import annotations

from typing import TYPE_CHECKING

from mongomig.cli.context import GlobalOptions, load_config, load_graph

if TYPE_CHECKING:
    from rich.console import Console

    from mongomig.output.console import Output


def run(opts: GlobalOptions, out: Output, *, revisions: list[str], lock_timeout: float) -> None:
    from mongomig.database.client import open_database
    from mongomig.migrations.executor import Executor

    config = load_config(opts, out)
    graph = load_graph(config)
    with open_database(config) as db:
        stamped = Executor(config, graph, db).stamp(revisions, lock_timeout=lock_timeout)

    def render(con: Console) -> None:
        if not stamped:
            con.print("Stamped [bold]<base>[/bold]: no revisions are marked as applied.")
            return
        con.print(
            f"Stamped {len(stamped)} revision(s) as applied (no migration code was run). "
            f"Current: [bold]{', '.join(revisions)}[/bold]"
        )

    out.result({"stamped": [s.revision for s in stamped]}, render)
