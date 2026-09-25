from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mongomig.cli.context import GlobalOptions, load_config
from mongomig.errors import ValidationError

if TYPE_CHECKING:
    from rich.console import Console

    from mongomig.output.console import Output
    from mongomig.schema.inference import InspectResult


def run(
    opts: GlobalOptions,
    out: Output,
    *,
    collections: list[str],
    sample_size: int | None,
    sample_percent: float | None,
    full_scan: bool,
) -> None:
    from mongomig.database.client import open_database
    from mongomig.schema.inference import inspect_collection, user_collections

    if (sample_size is not None) + (sample_percent is not None) + full_scan > 1:
        raise ValidationError("Use only one of --sample-size, --sample-percent, --full-scan.")

    config = load_config(opts, out)
    size = sample_size or config.settings.sampling.size
    results: list[InspectResult] = []
    with open_database(config) as db:
        names = collections or user_collections(db)
        existing = set(db.list_collection_names())
        missing = [n for n in names if n not in existing]
        if missing:
            raise ValidationError(
                f"Collection(s) not found in {db.name}: {', '.join(missing)}",
                suggestion="Run `mongomig inspect` without arguments to inspect all collections.",
            )
        for name in names:
            out.info(f"inspecting {name} ...")
            results.append(
                inspect_collection(
                    db,
                    name,
                    sample_size=size,
                    sample_percent=sample_percent,
                    full_scan=full_scan,
                )
            )
        db_name = db.name

    data = {"database": db_name, "collections": [_result_data(r) for r in results]}

    def render(con: Console) -> None:
        if not results:
            con.print(f"No collections in {db_name}.")
        for index, result in enumerate(results):
            if index:
                con.print()
            _render_result(con, result)

    out.result(data, render)


def _result_data(result: InspectResult) -> dict[str, Any]:
    return {
        "name": result.schema.name,
        "mode": result.mode,
        "documents_scanned": result.documents_scanned,
        "estimated_total": result.estimated_total,
        "complete": result.is_complete,
        "duration_s": round(result.duration_s, 3),
        **result.schema.to_dict(),
    }


def _render_result(con: Console, result: InspectResult) -> None:
    from rich.markup import escape

    from mongomig.output.schema_view import print_observed

    schema = result.schema
    con.print(f"[bold]Collection: {escape(schema.name)}[/bold]")
    if result.is_complete:
        how = f"{result.documents_scanned:,} (all)"
    else:
        how = f"{result.documents_scanned:,} random sample of ~{result.estimated_total:,}"
    con.print(f"Documents: {how}  [dim]({result.duration_s:.2f}s)[/dim]")

    if schema.fields:
        con.print("\nFields [dim](nested presence is relative to the parent object)[/dim]:")
        print_observed(con, schema.fields)

    con.print("\nIndexes:")
    for ix in schema.indexes:
        con.print(f"  {escape(ix.describe())}")
    if schema.validator:
        con.print(
            f"\nValidator: yes (level={schema.validation_level}, action={schema.validation_action})"
        )
    else:
        con.print("\nValidator: none")

    if not result.is_complete:
        con.print(
            f"\n[dim]Based on {result.documents_scanned:,} sampled documents; documents outside "
            "the sample may differ. Use --full-scan for a complete analysis.[/dim]"
        )
