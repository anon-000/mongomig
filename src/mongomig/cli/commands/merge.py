from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from mongomig.cli.context import GlobalOptions, load_config, load_graph, relpath
from mongomig.errors import ValidationError
from mongomig.migrations.revision import write_revision
from mongomig.schema.snapshot import snapshot_hash, write_snapshot

if TYPE_CHECKING:
    from rich.console import Console

    from mongomig.config.models import LoadedConfig
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

    rebuilt = _rebuild_snapshot(config)
    new_id, path = write_revision(
        config.versions_dir,
        message=message,
        down_revision=tuple(parents),
        snapshot_hash=rebuilt.hash if rebuilt else snapshot_hash(config.snapshot_path),
        rev_id=rev_id,
    )
    if rebuilt:  # only once the revision exists, like autogenerate
        write_snapshot(config.snapshot_path, rebuilt.data)
    shown = relpath(path, config)

    def render(con: Console) -> None:
        from rich.markup import escape

        con.print(f"[green]Created merge revision[/green] [bold]{new_id}[/bold] → {shown}")
        con.print(f"  merges: {', '.join(parents)}")
        if rebuilt is None:
            return
        if rebuilt.had_conflicts:
            con.print("  schema_snapshot.json had git conflict markers: rebuilt from the models")
            con.print(
                "  [dim]Make sure every model change since the branches split has a migration "
                "on one of them. Run `mongomig diff` afterwards: it should report no "
                "changes.[/dim]"
            )
            return
        if rebuilt.absorbed:
            con.print("  schema_snapshot.json rebuilt from the models; it now includes:")
            for change in rebuilt.absorbed:
                con.print(f"    {escape(change)}")
        else:
            con.print("  schema_snapshot.json already matched the models")
        con.print(
            "  [dim]Each change above should come from a migration on one of the merged "
            "branches. Run `mongomig diff` afterwards: it should report no changes.[/dim]"
        )

    out.result(
        {
            "revision": new_id,
            "down_revision": parents,
            "path": shown,
            "snapshot_rebuilt": rebuilt is not None,
            "snapshot_had_conflicts": bool(rebuilt and rebuilt.had_conflicts),
            "absorbed_changes": rebuilt.absorbed if rebuilt else [],
        },
        render,
    )


@dataclass
class _Rebuilt:
    data: dict[str, Any]
    hash: str
    had_conflicts: bool
    absorbed: list[str]


def _rebuild_snapshot(config: LoadedConfig) -> _Rebuilt | None:
    """After two branches merge, each side's migrations together match the merged models, but
    the snapshot file holds one side (or git conflict markers). Rebuild it from the models so
    `diff` doesn't report the other side's changes again."""
    from mongomig.config.envpy import load_metadata
    from mongomig.errors import ConfigError
    from mongomig.schema.diff import diff_schemas
    from mongomig.schema.snapshot import Snapshot, content_hash, load_snapshot

    metadata = load_metadata(config)
    if metadata is None:  # not using autogenerate: nothing to keep in sync
        return None
    declared, _ = metadata.schemas()
    data = Snapshot.from_schemas(declared, metadata.profile).to_dict()
    try:
        old = load_snapshot(config.snapshot_path)
    except ConfigError:
        return _Rebuilt(data, content_hash(data), had_conflicts=True, absorbed=[])
    changes = diff_schemas(old.collections, declared).changes
    absorbed = [f"{c.collection}: {c.summary}" for c in changes]
    return _Rebuilt(data, content_hash(data), had_conflicts=False, absorbed=absorbed)
