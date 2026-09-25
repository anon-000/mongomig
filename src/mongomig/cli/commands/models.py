from __future__ import annotations

import json
from typing import TYPE_CHECKING

from mongomig.cli.context import GlobalOptions, load_config

if TYPE_CHECKING:
    from rich.console import Console

    from mongomig.output.console import Output
    from mongomig.schema.models import CollectionSchema


def run(opts: GlobalOptions, out: Output) -> None:
    from mongomig.config.envpy import load_metadata

    config = load_config(opts, out)
    metadata = load_metadata(config)
    schemas: dict[str, CollectionSchema] = {}
    warnings: list[str] = []
    profile = None
    if metadata is not None:
        schemas, warnings = metadata.schemas()
        profile = metadata.profile.to_dict()

    data = {
        "storage": profile,
        "collections": {name: s.to_dict() for name, s in schemas.items()},
        "warnings": warnings,
    }

    def render(con: Console) -> None:
        from rich.markup import escape

        from mongomig.output.schema_view import print_declared

        if metadata is None:
            con.print(
                "No models registered: migrations/env.py sets target_metadata = None.\n"
                "See the comments in env.py to register your models."
            )
            return
        if not schemas:
            con.print("target_metadata has no collections registered.")
        for index, (name, schema) in enumerate(schemas.items()):
            if index:
                con.print()
            con.print(f"[bold]{escape(name)}[/bold]  [dim]{escape(schema.model or '')}[/dim]")
            print_declared(con, schema.fields)
            indexes = ", ".join(ix.describe() for ix in schema.indexes) or "none"
            con.print(f"  [dim]indexes:[/dim] {escape(indexes)}")
            if schema.validator is not None:
                con.print(
                    f"  [dim]validator:[/dim] managed (level={schema.validation_level}, "
                    f"action={schema.validation_action})"
                )
                if out.verbose:
                    con.print(escape(json.dumps(schema.validator, indent=2, default=str)))
        if profile:
            con.print(
                f"\n[dim]storage profile: {profile['mode']}"
                f"{', by_alias' if profile['by_alias'] else ''}"
                f"{', exclude_none' if profile['exclude_none'] else ''}"
                f"{', exclude_unset' if profile['exclude_unset'] else ''}[/dim]"
            )
        for warning in warnings:
            con.print(f"[yellow]warning:[/yellow] {escape(warning)}")

    out.result(data, render)
