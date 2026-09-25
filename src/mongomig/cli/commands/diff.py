from __future__ import annotations

from typing import TYPE_CHECKING

from mongomig.cli.context import GlobalOptions, load_config, load_graph
from mongomig.errors import ValidationError

if TYPE_CHECKING:
    from rich.console import Console

    from mongomig.output.console import Output
    from mongomig.schema.diff import DiffResult

SEVERITY_STYLE = {
    "SAFE": "green",
    "WARNING": "yellow",
    "REQUIRES_DATA_MIGRATION": "cyan",
    "MANUAL_REVIEW": "magenta",
    "DESTRUCTIVE": "red",
}


def run(opts: GlobalOptions, out: Output, *, check: bool, renames: list[str]) -> None:
    from mongomig.cli.commands._schema import compute

    config = load_config(opts, out)
    state = compute(config, load_graph(config), renames)
    diff = state.diff

    def render(con: Console) -> None:
        render_diff(con, diff, title="Schema changes (models vs migrations/schema_snapshot.json)")
        if diff.has_changes:
            con.print(
                '\nGenerate a migration with: mongomig revision --autogenerate -m "describe it"'
            )

    out.result(diff.to_dict(), render)
    if check and diff.has_changes:
        if out.json_mode:
            import typer

            raise typer.Exit(int(ValidationError.exit_code))
        raise ValidationError(
            f"{len(diff.changes)} model change(s) have no migration yet.",
            suggestion='Run `mongomig revision --autogenerate -m "..."` and commit the result.',
        )


def render_diff(con: Console, diff: DiffResult, *, title: str) -> None:
    from rich.markup import escape

    for warning in diff.warnings:
        con.print(f"[yellow]warning:[/yellow] {escape(warning)}")
    if not diff.has_changes:
        con.print("[green]No schema changes: models match the snapshot.[/green]")
        return

    con.print(f"{title}:\n")
    grouped = diff.by_collection()
    width = min(max(len(c.summary) for c in diff.changes) + 2, 60)
    for collection, changes in grouped.items():
        con.print(f"[bold]{escape(collection.upper())}[/bold]")
        for change in changes:
            style = SEVERITY_STYLE[change.severity.name]
            summary = escape(change.summary)
            con.print(
                f"  {summary:<{width}} [{style}]{change.severity.name}[/{style}]"
                f"  [dim]{escape(change.note)}[/dim]"
            )
        con.print()

    for hint in diff.rename_hints:
        con.print(
            f"[cyan]Possible rename:[/cyan] {escape(hint.collection)}.{escape(hint.old_path)} → "
            f"{escape(hint.new_path)} ({hint.similarity:.0%} similar). If so, pass "
            f"[bold]{escape(hint.flag)}[/bold]"
        )
    counts = " · ".join(f"{n} {name}" for name, n in diff.counts().items())
    con.print(f"[dim]{counts}[/dim]")
