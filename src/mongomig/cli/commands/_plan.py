"""Rendering of dry-run analyses (shared by `plan`, `upgrade --dry-run`, `downgrade --dry-run`)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rich.console import Console

    from mongomig.migrations.executor import MigrationAnalysis

RISK_STYLE = {"LOW": "green", "MEDIUM": "yellow", "HIGH": "red"}


def analyses_data(analyses: list[MigrationAnalysis]) -> dict[str, Any]:
    highest = max((a.assessment.risk for a in analyses), default=None)
    return {
        "migrations": [a.to_dict() for a in analyses],
        "estimated_docs": sum(a.estimated_docs for a in analyses),
        "risk": highest.name if highest is not None else None,
        "dry_run": True,
    }


def render_analyses(con: Console, analyses: list[MigrationAnalysis]) -> None:
    from rich.markup import escape

    if not analyses:
        con.print("[green]Nothing to do.[/green]")
        return

    for analysis in analyses:
        script = analysis.script
        risk = analysis.assessment.risk.name
        style = RISK_STYLE[risk]
        verb = "" if analysis.direction == "upgrade" else "downgrade "
        con.print(
            f"\n[bold]{verb}{script.revision}[/bold]  {escape(script.message)}   "
            f"Risk: [{style}]{risk}[/{style}]"
        )
        rows = [_op_row(op) for op in analysis.ops]
        if rows:
            w_coll = max(len(r[0]) for r in rows) + 2
            w_op = max(len(r[1]) for r in rows) + 2
            w_detail = min(max(len(r[2]) for r in rows) + 2, 48)
            for (coll, op, detail, impact), recorded in zip(rows, analysis.ops, strict=True):
                con.print(
                    f"  {escape(coll):<{w_coll}}{escape(op):<{w_op}}"
                    f"{escape(detail):<{w_detail}}[dim]{escape(impact)}[/dim]"
                )
                for warning in recorded.warnings:
                    con.print(f"  {'':<{w_coll}}[yellow]⚠ {escape(warning)}[/yellow]")
        else:
            con.print("  [dim]no database operations[/dim]")
        if analysis.unavailable:
            con.print(f"  [yellow]not fully simulated:[/yellow] {escape(analysis.unavailable)}")
        if analysis.error:
            con.print(f"  [red]dry run raised:[/red] {escape(analysis.error)}")

        resumable = {True: "yes (ctx.ops are idempotent)", None: "unknown"}[analysis.resumable]
        con.print(
            f"  [dim]reversible: {'yes' if script.reversible else 'no'} · "
            f"deletes data: {'YES' if analysis.destructive else 'no'} · "
            f"resumable: {resumable}[/dim]"
        )
        if analysis.assessment.reasons:
            con.print(f"  [dim]why {risk}:[/dim] {escape('; '.join(analysis.assessment.reasons))}")

    data = analyses_data(analyses)
    style = RISK_STYLE[data["risk"]]
    con.print(
        f"\n{len(analyses)} migration(s) · ~{data['estimated_docs']:,} document writes · "
        f"highest risk [{style}]{data['risk']}[/{style}]"
    )
    note = "Estimates only; nothing was changed."
    if len(analyses) > 1:
        note += (
            " Each migration is simulated against the current data, so later ones are approximate."
        )
    con.print(f"[dim]{note}[/dim]")


def _op_row(op: Any) -> tuple[str, str, str, str]:
    impact: list[str] = []
    if op.estimated_docs is not None:
        prefix = "" if op.operation.startswith("create_index") else "~"
        impact.append(f"{prefix}{op.estimated_docs:,} docs")
    if op.collection_scan:
        impact.append("collection scan")
    if op.calls > 1:
        impact.append(f"{op.calls:,} calls")
    if op.destructive:
        impact.append("DELETES DATA")
    if not op.exact:
        impact.append("custom code")
    return op.collection, op.operation, op.detail, " · ".join(impact)
