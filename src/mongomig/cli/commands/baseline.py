from __future__ import annotations

from typing import TYPE_CHECKING

from mongomig.cli.context import GlobalOptions, load_config, load_graph, relpath
from mongomig.errors import ValidationError

if TYPE_CHECKING:
    from rich.console import Console

    from mongomig.output.console import Output

BASELINE_NOTES = """\
Baseline: records the current models as the starting point for an existing database.
It changes nothing in MongoDB; `mongomig upgrade` just marks it as applied.
Later `revision --autogenerate` runs diff against this state."""


def run(opts: GlobalOptions, out: Output, *, message: str, force: bool) -> None:
    from mongomig.cli.commands._schema import compute
    from mongomig.migrations.revision import write_revision
    from mongomig.schema.snapshot import content_hash, write_snapshot

    config = load_config(opts, out)
    graph = load_graph(config)
    state = compute(config, graph, [])
    if state.snapshot.collections and not force:
        raise ValidationError(
            "schema_snapshot.json already describes collections; a baseline would discard it.",
            suggestion='Use `mongomig revision --autogenerate -m "..."` for model changes, or '
            "--force to overwrite the snapshot with the current models.",
        )
    parent = graph.single_head()
    snapshot_data = state.new_snapshot.to_dict()
    rev_id, path = write_revision(
        config.versions_dir,
        message=message,
        down_revision=parent,
        snapshot_hash=content_hash(snapshot_data),
        notes=BASELINE_NOTES,
        upgrade_body="    pass",
        downgrade_body="    pass",
    )
    write_snapshot(config.snapshot_path, snapshot_data)
    shown = relpath(path, config)
    names = sorted(state.declared)

    def render(con: Console) -> None:
        con.print(f"[green]Created baseline revision[/green] [bold]{rev_id}[/bold] → {shown}")
        con.print(f"  snapshot: {len(names)} collection(s): {', '.join(names) or '-'}")
        for warning in state.diff.warnings:
            con.print(f"[yellow]warning:[/yellow] {warning}")
        con.print("\nNext: `mongomig upgrade` (marks the baseline applied; changes nothing).")

    out.result(
        {"revision": rev_id, "path": shown, "collections": names, "warnings": state.diff.warnings},
        render,
    )
