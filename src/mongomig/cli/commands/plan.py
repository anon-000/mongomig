from __future__ import annotations

from typing import TYPE_CHECKING

from mongomig.cli.commands._plan import analyses_data, render_analyses
from mongomig.cli.context import GlobalOptions, load_config, load_graph

if TYPE_CHECKING:
    from rich.console import Console

    from mongomig.output.console import Output


def run(opts: GlobalOptions, out: Output, *, target: str, steps: int | None) -> None:
    from mongomig.database.client import open_database
    from mongomig.migrations.executor import Executor

    config = load_config(opts, out)
    graph = load_graph(config)
    with open_database(config) as db:
        executor = Executor(config, graph, db)
        state = executor.state()
        analyses = executor.analyze("upgrade", target, steps=steps)
        db_name = db.name

    order = graph.topological_order()
    status = {rev: ("applied" if rev in state.applied else "pending") for rev in order}

    def render(con: Console) -> None:
        from rich.markup import escape

        env = f" ({config.environment})" if config.environment else ""
        con.print(f"[bold]Migration plan for {escape(db_name)}{escape(env)}[/bold]\n")
        for rev in order:
            mark = (
                "[green]applied[/green]" if status[rev] == "applied" else "[yellow]pending[/yellow]"
            )
            con.print(f"  {rev}  {escape(graph.scripts[rev].message):<40} {mark}")
        render_analyses(con, analyses)

    out.result({"database": db_name, "status": status, **analyses_data(analyses)}, render)
