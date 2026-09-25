"""Progress/event reporting for migration runs.

The executor and ``ctx.ops`` talk to a ``Reporter``; the CLI plugs in a rich console reporter,
the Python API defaults to the standard ``logging`` module.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from mongomig.migrations.script import Script

Direction = Literal["upgrade", "downgrade"]

logger = logging.getLogger("mongomig")


class Reporter:
    """No-op base. Override what you need."""

    def migration_started(self, script: Script, direction: Direction) -> None:
        pass

    def migration_finished(self, script: Script, direction: Direction, duration_ms: int) -> None:
        pass

    def migration_failed(self, script: Script, direction: Direction, error: BaseException) -> None:
        pass

    def log(self, message: str) -> None:
        pass

    def warn(self, message: str) -> None:
        pass

    def progress(self, task: str, done: int, total: int | None) -> None:
        pass

    def progress_done(self, task: str) -> None:
        pass


class LoggingReporter(Reporter):
    def migration_started(self, script: Script, direction: Direction) -> None:
        logger.info("Running %s %s: %s", direction, script.revision, script.message)

    def migration_finished(self, script: Script, direction: Direction, duration_ms: int) -> None:
        logger.info("Finished %s %s in %d ms", direction, script.revision, duration_ms)

    def migration_failed(self, script: Script, direction: Direction, error: BaseException) -> None:
        logger.error("Failed %s %s: %s", direction, script.revision, error)

    def log(self, message: str) -> None:
        logger.info(message)

    def warn(self, message: str) -> None:
        logger.warning(message)

    def progress(self, task: str, done: int, total: int | None) -> None:
        logger.debug("%s: %d/%s", task, done, "?" if total is None else total)
