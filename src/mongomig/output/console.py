"""Single place that decides how results, warnings and errors reach the terminal.

Human mode renders with rich (colour only on a TTY). ``--json`` mode writes exactly one JSON
document to stdout per command; warnings go to stderr so stdout stays machine-parseable.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TextIO

if TYPE_CHECKING:
    from rich.console import Console

    from mongomig.errors import MongoMigError


class Output:
    def __init__(
        self,
        *,
        json_mode: bool = False,
        verbose: bool = False,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> None:
        self.json_mode = json_mode
        self.verbose = verbose
        self._stdout = stdout
        self._stderr = stderr
        self._console: Console | None = None
        self._err_console: Console | None = None

    @property
    def console(self) -> Console:
        if self._console is None:
            from rich.console import Console

            self._console = Console(
                file=self._stdout or sys.stdout, highlight=False, soft_wrap=True
            )
        return self._console

    @property
    def err_console(self) -> Console:
        if self._err_console is None:
            from rich.console import Console

            self._err_console = Console(
                file=self._stderr or sys.stderr, highlight=False, soft_wrap=True
            )
        return self._err_console

    def result(self, data: Any, render: Callable[[Console], None]) -> None:
        """Emit a command result: ``data`` as JSON, or ``render`` for humans."""
        if self.json_mode:
            stream = self._stdout or sys.stdout
            stream.write(json.dumps(data, indent=2, default=str) + "\n")
        else:
            render(self.console)

    def warn(self, message: str) -> None:
        if self.json_mode:
            (self._stderr or sys.stderr).write(f"warning: {message}\n")
        else:
            self.err_console.print(f"[yellow]warning:[/yellow] {message}")

    def info(self, message: str) -> None:
        """Verbose-only progress messages."""
        if self.verbose and not self.json_mode:
            self.err_console.print(f"[dim]{message}[/dim]")

    def error(self, err: MongoMigError) -> None:
        if self.json_mode:
            stream = self._stdout or sys.stdout
            stream.write(json.dumps({"error": err.to_dict()}, indent=2, default=str) + "\n")
            return
        from rich.markup import escape

        con = self.err_console
        con.print(f"[bold red]error:[/bold red] {escape(err.message)}")
        if self.verbose and err.details:
            for key, value in err.details.items():
                con.print(f"  [dim]{key}:[/dim] {escape(str(value))}")
        if err.suggestion:
            con.print(f"[cyan]hint:[/cyan] {escape(err.suggestion)}")
