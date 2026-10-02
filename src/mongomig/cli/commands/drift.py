from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mongomig.cli.context import GlobalOptions, load_config, load_graph
from mongomig.errors import ValidationError

if TYPE_CHECKING:
    from rich.console import Console

    from mongomig.output.console import Output
    from mongomig.schema.drift import CollectionDrift


def run(
    opts: GlobalOptions,
    out: Output,
    *,
    collections: list[str],
    sample_size: int | None,
    sample_percent: float | None,
    full_scan: bool,
    check: bool,
    strict: bool,
) -> None:
    from mongomig.cli.commands._schema import require_metadata
    from mongomig.database.client import open_database
    from mongomig.migrations.tracker import MigrationTracker, compute_state
    from mongomig.schema.drift import Thresholds, check_collections

    if (sample_size is not None) + (sample_percent is not None) + full_scan > 1:
        raise ValidationError("Use only one of --sample-size, --sample-percent, --full-scan.")

    config = load_config(opts, out)
    graph = load_graph(config)
    declared, _ = require_metadata(config).schemas()
    unknown = [c for c in collections if c not in declared]
    if unknown:
        raise ValidationError(
            f"Not a registered collection: {', '.join(unknown)}",
            suggestion="`mongomig models` lists the collections your models declare.",
        )

    configured = config.settings.drift.thresholds
    thresholds = (
        Thresholds(0, 0, 0)
        if strict
        else Thresholds(
            configured.missing_field_percent,
            configured.unexpected_type_percent,
            configured.unexpected_field_percent,
        )
    )
    with open_database(config) as db:
        records = MigrationTracker(db, config.settings.migrations.tracking_collection).records()
        results = check_collections(
            db,
            declared,
            thresholds,
            only=collections or None,
            sample_size=sample_size or config.settings.sampling.size,
            sample_percent=sample_percent,
            full_scan=full_scan,
        )
        db_name = db.name
    pending = compute_state(graph, records).pending

    failed = sum(len(r.failed) for r in results)
    data: dict[str, Any] = {
        "database": db_name,
        "pending_migrations": pending,
        "thresholds": {
            "missing_field_percent": thresholds.missing_field_percent,
            "unexpected_type_percent": thresholds.unexpected_type_percent,
            "unexpected_field_percent": thresholds.unexpected_field_percent,
        },
        "failed": failed,
        "collections": [r.to_dict() for r in results],
    }

    def render(con: Console) -> None:
        render_drift(con, results, pending, out.verbose)

    out.result(data, render)
    if check and failed:
        import typer

        raise typer.Exit(int(ValidationError.exit_code))


def render_drift(
    con: Console, results: list[CollectionDrift], pending: list[str], verbose: bool
) -> None:
    from rich.markup import escape

    if pending:
        con.print(
            f"[yellow]warning:[/yellow] {len(pending)} migration(s) pending; part of the drift "
            "below may simply be changes not applied yet (`mongomig upgrade`)."
        )
    for result in results:
        if not result.exists:
            sample = "not in the database"
        elif result.complete:
            sample = f"all {result.documents_scanned:,} documents"
        else:
            sample = (
                f"{result.documents_scanned:,} sampled of ~{result.estimated_total:,} documents"
            )
        con.print(f"\n[bold]{escape(result.name.upper())}[/bold]  [dim]{sample}[/dim]")
        if not result.findings:
            con.print("  [green]✓ matches the model[/green]")
            continue
        width = min(max(len(_label(f)) for f in result.findings) + 2, 32)
        for f in sorted(result.findings, key=lambda f: (f.status != "fail", f.path or "")):
            icon = "[red]✗[/red]" if f.status == "fail" else "[yellow]![/yellow]"
            limit = ""
            if f.threshold is not None and f.share is not None:
                limit = f"  [dim]{'>' if f.status == 'fail' else '≤'} {f.threshold:g}%[/dim]"
            label = _label(f)
            con.print(f"  {icon} {escape(label):<{width}}{escape(f.summary)}{limit}")
            if f.hint and (f.status == "fail" or verbose):
                con.print(f"    {'':<{width}}[cyan]hint:[/cyan] {escape(f.hint)}")

    failed = sum(len(r.failed) for r in results)
    warnings = sum(len(r.findings) for r in results) - failed
    style = "red" if failed else ("yellow" if warnings else "green")
    con.print(f"\n[{style}]{failed} failed, {warnings} below threshold[/{style}]")
    if any(r.exists and not r.complete for r in results):
        con.print(
            "[dim]Based on samples: documents outside the sample may differ. "
            "Use --full-scan for an exact answer.[/dim]"
        )


def _label(finding: Any) -> str:
    """Field path, or what a structural finding is about."""
    if finding.path:
        return str(finding.path)
    if finding.kind.startswith("index_"):
        return "(index)"
    if finding.kind.startswith("validator_"):
        return "(validator)"
    return "(collection)"
