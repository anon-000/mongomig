from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from mongomig.cli.commands._runs import render_run_result
from mongomig.cli.context import GlobalOptions, load_config, load_graph
from mongomig.errors import ConfirmationRequiredError

if TYPE_CHECKING:
    from mongomig.migrations.script import Script
    from mongomig.output.console import Output


def run(
    opts: GlobalOptions,
    out: Output,
    *,
    target: str | None,
    steps: int | None,
    yes: bool,
    force: bool,
    lock_timeout: float,
    dry_run: bool = False,
) -> None:
    from mongomig.cli.reporting import ConsoleReporter
    from mongomig.database.client import open_database
    from mongomig.migrations.executor import Executor

    config = load_config(opts, out)
    graph = load_graph(config)

    if dry_run:
        from mongomig.cli.commands._plan import analyses_data, render_analyses

        with open_database(config) as db:
            analyses = Executor(config, graph, db).analyze("downgrade", target, steps=steps)
        out.result(analyses_data(analyses), lambda con: render_analyses(con, analyses))
        return

    def confirm(plan: list[Script]) -> bool:
        if yes:
            return True
        if out.json_mode or not sys.stdin.isatty():
            raise ConfirmationRequiredError(
                f"Downgrade would revert {len(plan)} revision(s) and needs confirmation.",
                suggestion="Re-run with --yes to confirm non-interactively.",
                details={"revisions": [s.revision for s in plan]},
            )
        import typer

        target_db = config.settings.database.name or "(from URI)"
        env = f" [{config.environment}]" if config.environment else ""
        out.console.print(f"About to revert on database [bold]{target_db}[/bold]{env}:")
        for script in plan:
            flag = "" if script.reversible else "  [red](irreversible)[/red]"
            out.console.print(f"  - {script.revision}  {script.message}{flag}")
        return typer.confirm("Proceed?", default=False)

    with open_database(config) as db:
        executor = Executor(config, graph, db, reporter=ConsoleReporter(out))
        result = executor.downgrade(
            target,
            steps=steps,
            allow_irreversible=force,
            confirm=confirm,
            lock_timeout=lock_timeout,
        )
    render_run_result(out, result, "Nothing to downgrade.")
