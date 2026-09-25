"""Check lists for `validate` and `doctor`: each check is ok / warn / fail / skip."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from rich.console import Console

    from mongomig.output.console import Output

Status = Literal["ok", "warn", "fail", "skip"]
ICON = {
    "ok": "[green]✓[/green]",
    "warn": "[yellow]![/yellow]",
    "fail": "[red]✗[/red]",
    "skip": "[dim]-[/dim]",
}


@dataclass
class Check:
    name: str
    status: Status
    detail: str = ""
    hint: str = ""


@dataclass
class CheckList:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, status: Status, detail: str = "", hint: str = "") -> Check:
        check = Check(name, status, detail, hint)
        self.checks.append(check)
        return check

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if c.status == "fail"]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.status == "warn"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": not self.failed,
            "failures": len(self.failed),
            "warnings": len(self.warnings),
            "checks": [
                {"name": c.name, "status": c.status, "detail": c.detail, "hint": c.hint}
                for c in self.checks
            ],
        }

    def render(self, con: Console) -> None:
        from rich.markup import escape

        width = max((len(c.name) for c in self.checks), default=0) + 2
        for c in self.checks:
            con.print(f"{ICON[c.status]} {escape(c.name):<{width}}{escape(c.detail)}")
            if c.hint and c.status in ("warn", "fail"):
                con.print(f"  {'':<{width}}[cyan]hint:[/cyan] {escape(c.hint)}")
        summary = f"{len(self.failed)} failed, {len(self.warnings)} warning(s)"
        style = "red" if self.failed else ("yellow" if self.warnings else "green")
        con.print(f"\n[{style}]{summary}[/{style}]")


def finish(out: Output, checks: CheckList) -> None:
    """Emit the result; exit 1 when any check failed (the report already says why)."""
    from mongomig.errors import ValidationError

    out.result(checks.to_dict(), checks.render)
    if checks.failed:
        import typer

        raise typer.Exit(int(ValidationError.exit_code))
