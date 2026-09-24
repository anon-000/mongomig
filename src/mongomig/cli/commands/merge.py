from __future__ import annotations

from typing import TYPE_CHECKING

from mongomig.cli.context import GlobalOptions, load_config, load_graph, relpath
from mongomig.errors import ValidationError
from mongomig.migrations.revision import write_revision
from mongomig.schema.snapshot import snapshot_hash

if TYPE_CHECKING:
    from rich.console import Console

    from mongomig.output.console import Output


def run(
    opts: GlobalOptions, out: Output, *, revisions: list[str], message: str, rev_id: str | None
) -> None:
    config = load_config(opts, out)
    graph = load_graph(config)

    parents = [graph.resolve(r) for r in revisions] if revisions else graph.heads()
    parents = list(dict.fromkeys(parents))  # dedupe, keep order
    if len(parents) < 2:
        raise ValidationError(
            "Nothing to merge: need at least two revisions (there is only one head).",
            suggestion="`mongomig heads` shows the current heads.",
        )
    for a in parents:
        for b in parents:
            if a != b and a in graph.ancestors(b):
                raise ValidationError(
                    f"{a} is already an ancestor of {b}; merging them is meaningless.",
                )

    new_id, path = write_revision(
        config.versions_dir,
        message=message,
        down_revision=tuple(parents),
        snapshot_hash=snapshot_hash(config.snapshot_path),
        rev_id=rev_id,
    )
    shown = relpath(path, config)

    def render(con: Console) -> None:
        con.print(f"[green]Created merge revision[/green] [bold]{new_id}[/bold] → {shown}")
        con.print(f"  merges: {', '.join(parents)}")

    out.result({"revision": new_id, "down_revision": parents, "path": shown}, render)
