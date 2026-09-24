from __future__ import annotations

from typing import TYPE_CHECKING

from mongomig.cli.context import GlobalOptions, load_config, load_graph, relpath
from mongomig.errors import RevisionConflictError
from mongomig.migrations.revision import write_revision
from mongomig.schema.snapshot import snapshot_hash

if TYPE_CHECKING:
    from rich.console import Console

    from mongomig.output.console import Output


def run(
    opts: GlobalOptions, out: Output, *, message: str, head: str | None, rev_id: str | None
) -> None:
    config = load_config(opts, out)
    graph = load_graph(config)

    parent = graph.resolve(head) if head else graph.single_head()
    if head and parent is not None and graph.children[parent]:
        out.warn(f"{parent} already has children; this creates a new branch (a second head).")
    if rev_id and rev_id in graph:
        raise RevisionConflictError(f"Revision id {rev_id!r} already exists.")

    new_id, path = write_revision(
        config.versions_dir,
        message=message,
        down_revision=parent,
        snapshot_hash=snapshot_hash(config.snapshot_path),
        rev_id=rev_id,
    )
    shown = relpath(path, config)

    def render(con: Console) -> None:
        con.print(f"[green]Created revision[/green] [bold]{new_id}[/bold] → {shown}")
        con.print(f"  revises: {parent or '<base>'}")

    out.result({"revision": new_id, "down_revision": parent, "path": shown}, render)
