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
    target: str,
    steps: int | None,
    lock_timeout: float,
    dry_run: bool = False,
    yes: bool = False,
) -> None:
    from mongomig.cli.reporting import ConsoleReporter
    from mongomig.database.client import open_database
    from mongomig.migrations.executor import Executor

    config = load_config(opts, out)
    graph = load_graph(config)

    if dry_run:
        from mongomig.cli.commands._plan import analyses_data, render_analyses

        with open_database(config) as db:
            analyses = Executor(config, graph, db).analyze("upgrade", target, steps=steps)
        out.result(analyses_data(analyses), lambda con: render_analyses(con, analyses))
        return

    def confirm(plan: list[Script], reasons: list[str]) -> bool:
        if yes:
            return True
        if out.json_mode or not sys.stdin.isatty():
            raise ConfirmationRequiredError(
                "These migrations need confirmation: " + "; ".join(reasons),
                suggestion="Review them with `mongomig plan`, then re-run with --yes.",
                details={"reasons": reasons, "revisions": [s.revision for s in plan]},
            )
        import typer
        from rich.markup import escape

        env = f" [{config.environment}]" if config.environment else ""
        out.console.print(f"[bold]About to apply {len(plan)} revision(s){escape(env)}:[/bold]")
        for script in plan:
            out.console.print(f"  - {script.revision}  {escape(script.message)}")
        for reason in reasons:
            out.console.print(f"  [yellow]⚠ {escape(reason)}[/yellow]")
        out.console.print("[dim]Tip: `mongomig plan` shows the full impact first.[/dim]")
        return typer.confirm("Proceed?", default=False)

    with open_database(config) as db:
        executor = Executor(config, graph, db, reporter=ConsoleReporter(out))
        result = executor.upgrade(target, steps=steps, lock_timeout=lock_timeout, confirm=confirm)
    render_run_result(out, result, "[green]Already up to date.[/green]")
