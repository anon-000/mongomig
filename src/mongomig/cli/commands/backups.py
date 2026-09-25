from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

from mongomig.cli.context import GlobalOptions, load_config
from mongomig.errors import ConfirmationRequiredError, ValidationError

if TYPE_CHECKING:
    from rich.console import Console

    from mongomig.output.console import Output


def run(opts: GlobalOptions, out: Output, *, drop: list[str], yes: bool) -> None:
    from mongomig.database.client import open_database
    from mongomig.migrations.ops import BACKUP_PREFIX

    config = load_config(opts, out)
    with open_database(config) as db:
        names = sorted(n for n in db.list_collection_names() if n.startswith(BACKUP_PREFIX))
        rows: list[dict[str, Any]] = []
        for name in names:
            stats = db.command("collStats", name)
            rows.append(
                {
                    "name": name,
                    "documents": stats.get("count", 0),
                    "size_bytes": stats.get("size", 0),
                }
            )

        dropped: list[str] = []
        if drop:
            selected = [
                n for n in names if any(n == d or n.startswith(BACKUP_PREFIX + d) for d in drop)
            ]
            if not selected:
                raise ValidationError(
                    f"No backup matches {', '.join(drop)}.",
                    suggestion="Run `mongomig backups` to list them.",
                )
            if not yes:
                if out.json_mode or not sys.stdin.isatty():
                    raise ConfirmationRequiredError(
                        f"Dropping {len(selected)} backup collection(s) deletes them for good.",
                        suggestion="Re-run with --yes to confirm.",
                    )
                import typer

                for n in selected:
                    out.console.print(f"  - {n}")
                if not typer.confirm("Drop these backups permanently?", default=False):
                    return
            for n in selected:
                db.drop_collection(n)
                dropped.append(n)

    def render(con: Console) -> None:
        if dropped:
            con.print(f"[green]Dropped {len(dropped)} backup(s).[/green]")
            return
        if not rows:
            con.print("No backups. (Created by unset_field/drop_collection with backup=True.)")
            return
        for row in rows:
            con.print(
                f"  {row['name']:<60} {row['documents']:>10,} docs  "
                f"{row['size_bytes'] / 1_048_576:>8.1f} MB"
            )
        con.print("\n[dim]Drop with: mongomig backups --drop <revision-or-name> --yes[/dim]")

    out.result({"backups": rows, "dropped": dropped}, render)
