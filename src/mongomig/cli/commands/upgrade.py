from __future__ import annotations

from typing import TYPE_CHECKING

from mongomig.cli.commands._runs import render_run_result
from mongomig.cli.context import GlobalOptions, load_config, load_graph

if TYPE_CHECKING:
    from mongomig.output.console import Output


def run(
    opts: GlobalOptions, out: Output, *, target: str, steps: int | None, lock_timeout: float
) -> None:
    from mongomig.cli.reporting import ConsoleReporter
    from mongomig.database.client import open_database
    from mongomig.migrations.executor import Executor

    config = load_config(opts, out)
    graph = load_graph(config)
    with open_database(config) as db:
        executor = Executor(config, graph, db, reporter=ConsoleReporter(out))
        result = executor.upgrade(target, steps=steps, lock_timeout=lock_timeout)
    render_run_result(out, result, "[green]Already up to date.[/green]")
