"""`mongomig squash [TO]`: replace the revisions from the base up to TO with one revision."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from mongomig.cli.context import GlobalOptions, load_config, load_graph, relpath

if TYPE_CHECKING:
    from rich.console import Console

    from mongomig.output.console import Output


def run(
    opts: GlobalOptions,
    out: Output,
    *,
    to: str | None,
    message: str,
    rev_id: str | None,
    dry_run: bool,
) -> None:
    from mongomig.errors import RevisionConflictError
    from mongomig.migrations.graph import build_graph
    from mongomig.migrations.revision import write_revision
    from mongomig.migrations.script import archive_dir
    from mongomig.migrations.squash import analyze, linear_chain, render_bodies

    config = load_config(opts, out)
    graph = load_graph(config)
    target = graph.resolve(to) if to else graph.single_head()
    if target is None:
        from mongomig.errors import ValidationError

        raise ValidationError("Nothing to squash: there are no revisions yet.")
    chain = linear_chain(graph, target)
    scripts = [graph.scripts[rev] for rev in chain]
    if rev_id and rev_id in graph:
        raise RevisionConflictError(f"Revision id {rev_id!r} already exists.")

    analysis = analyze(scripts)
    upgrade_body, downgrade_body = render_bodies(analysis)
    notes = [
        f"Squashed from {len(chain)} revisions ({chain[0]} … {chain[-1]}):",
        *[f"  {s.revision}  {s.message}" for s in scripts],
        "",
        "A new, empty database runs this revision instead of the ones above: it creates the",
        "collections, indexes and validators they build up. A database that already ran them",
        "adopts this revision on its next upgrade without running anything.",
        f"Data operations skipped (nothing to change in an empty database): "
        f"{analysis.data_ops_skipped}.",
    ]
    if analysis.review:
        notes += ["", "Needs review (see TODO(review) in upgrade):"]
        notes += [f"  - {item}" for item in analysis.review]

    summary = {
        "replaces": chain,
        "collections": list(analysis.collections),
        "indexes": analysis.index_count,
        "validators": analysis.validator_count,
        "data_ops_skipped": analysis.data_ops_skipped,
        "review": analysis.review,
        "dry_run": dry_run,
    }

    new_id = path_shown = archive_shown = None
    if not dry_run:
        new_id, path = write_revision(
            config.versions_dir,
            message=message,
            down_revision=None,
            snapshot_hash=scripts[-1].snapshot_hash,
            rev_id=rev_id,
            upgrade_body=upgrade_body,
            downgrade_body=downgrade_body,
            notes="\n".join(notes),
            reversible=all(s.reversible for s in scripts),
            replaces=tuple(chain),
        )
        archive = archive_dir(config.versions_dir, new_id)
        archive.mkdir(parents=True)
        for script in scripts:
            shutil.move(str(script.path), archive / script.path.name)
        build_graph(config.versions_dir)  # the new graph must load (children re-linked)
        path_shown, archive_shown = relpath(path, config), relpath(archive, config)
    summary.update(revision=new_id, path=path_shown, archive=archive_shown)

    def render(con: Console) -> None:
        from rich.markup import escape

        verb = "Would squash" if dry_run else "Squashed"
        con.print(f"[green]{verb} {len(chain)} revisions[/green] ({chain[0]} … {chain[-1]})")
        if not dry_run:
            con.print(f"  new revision: [bold]{new_id}[/bold] → {path_shown}")
            con.print(f"  replaced files moved to: {archive_shown}/")
        con.print(
            f"  schema: {len(analysis.collections)} collection(s), {analysis.index_count} "
            f"index(es), {analysis.validator_count} validator(s); "
            f"{analysis.data_ops_skipped} data operation(s) skipped"
        )
        for item in analysis.review:
            con.print(f"  [magenta]review:[/magenta] {escape(item)}")
        if not dry_run:
            con.print(
                "\nNext: review the new file, run `mongomig validate`, commit. Databases that "
                "ran the old revisions adopt the squash on their next `upgrade`. Delete "
                f"{archive_shown}/ once every environment has upgraded past {chain[-1]}."
            )

    out.result(summary, render)
