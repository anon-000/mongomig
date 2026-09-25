"""Shared rendering for commands that run migrations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rich.console import Console

    from mongomig.migrations.executor import RunResult
    from mongomig.output.console import Output


def run_result_data(result: RunResult) -> dict[str, Any]:
    return {
        "direction": result.direction,
        "aborted": result.aborted,
        "applied" if result.direction == "upgrade" else "reverted": [
            {
                "revision": s.revision,
                "message": s.message,
                "duration_ms": s.duration_ms,
                "skipped_code": s.skipped_code,
            }
            for s in result.steps
        ],
    }


def render_run_result(out: Output, result: RunResult, nothing_message: str) -> None:
    def render(con: Console) -> None:
        if result.aborted:
            con.print("[yellow]Aborted; nothing changed.[/yellow]")
        elif not result.steps:
            con.print(nothing_message)
        else:
            verb = "Applied" if result.direction == "upgrade" else "Reverted"
            con.print(f"[green]{verb} {len(result.steps)} revision(s).[/green]")

    out.result(run_result_data(result), render)
