"""Console reporter: live migration progress for humans, silence for ``--json``."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from mongomig.migrations.reporting import Direction, Reporter

if TYPE_CHECKING:
    from mongomig.migrations.script import Script
    from mongomig.output.console import Output

PROGRESS_INTERVAL_S = 2.0


class ConsoleReporter(Reporter):
    def __init__(self, out: Output) -> None:
        self.out = out
        self._task_start: dict[str, float] = {}
        self._last_print: dict[str, float] = {}
        self._last_done: dict[str, tuple[int, int | None]] = {}

    @property
    def _quiet(self) -> bool:
        return self.out.json_mode

    def migration_started(self, script: Script, direction: Direction) -> None:
        if self._quiet:
            return
        if direction == "upgrade":
            parents = ", ".join(script.down_revisions) or "<base>"
            arrow = f"{parents} -> {script.revision}"
        else:
            arrow = f"{script.revision} -> {', '.join(script.down_revisions) or '<base>'}"
        self.out.console.print(f"[bold]Running {direction}[/bold] {arrow}, {script.message}")

    def migration_finished(self, script: Script, direction: Direction, duration_ms: int) -> None:
        if not self._quiet:
            self.out.console.print(f"  [green]✓[/green] done in {_duration(duration_ms / 1000)}")

    def migration_failed(self, script: Script, direction: Direction, error: BaseException) -> None:
        if not self._quiet:
            self.out.console.print("  [red]✗ failed[/red]")

    def log(self, message: str) -> None:
        if not self._quiet:
            from rich.markup import escape

            self.out.console.print(f"  [dim]• {escape(message)}[/dim]")

    def warn(self, message: str) -> None:
        self.out.warn(message)

    def progress(self, task: str, done: int, total: int | None) -> None:
        now = time.monotonic()
        self._last_done[task] = (done, total)
        if task not in self._task_start:  # first call marks the start; nothing to show yet
            self._task_start[task] = now
            self._last_print[task] = now
            return
        if now - self._last_print[task] >= PROGRESS_INTERVAL_S:
            self._last_print[task] = now
            self._print_progress(task, now)

    def progress_done(self, task: str) -> None:
        start, last = self._task_start.get(task), self._last_print.get(task)
        if start is not None and last is not None and last > start:
            self._print_progress(task, time.monotonic())  # final line only if we printed before
        for d in (self._task_start, self._last_print, self._last_done):
            d.pop(task, None)

    def _print_progress(self, task: str, now: float) -> None:
        if self._quiet:
            return
        done, total = self._last_done[task]
        elapsed = max(now - self._task_start[task], 1e-6)
        rate = done / elapsed
        parts = [f"{task}: {done:,}"]
        if total:
            parts[0] += f" / ~{total:,} ({min(done / total, 1):.1%})"
        parts.append(f"{rate:,.0f} docs/s")
        parts.append(f"elapsed {_duration(elapsed)}")
        if total and rate > 0 and done < total:
            parts.append(f"ETA {_duration((total - done) / rate)}")
        self.out.console.print("  " + " · ".join(parts))


def _duration(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"
