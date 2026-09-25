"""``ctx``: what every ``upgrade(ctx)`` / ``downgrade(ctx)`` receives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from mongomig.migrations.reporting import Direction, Reporter

if TYPE_CHECKING:
    from pymongo.collection import Collection
    from pymongo.database import Database

    from mongomig.migrations.dryrun import Recorder
    from mongomig.migrations.lock import MigrationLock
    from mongomig.migrations.ops import Operations


@dataclass
class OperationState:
    """What the context is doing right now; used to build helpful error messages."""

    operation: str
    collection: str | None = None
    processed: int = 0
    batch: int = 0


class MigrationContext:
    """Entry point for migration code.

    - ``ctx.ops``: high-level, batched, idempotent operations (preferred).
    - ``ctx.collection(name)``: a plain PyMongo ``Collection`` for custom logic.
    - ``ctx.unsafe_db``: the raw PyMongo ``Database`` (escape hatch; future dry-runs can't
      see what you do with it).

    Usable on its own too::

        ctx = MigrationContext(client["app"])
        ctx.ops.create_index("users", "email", unique=True)
    """

    def __init__(
        self,
        db: Database[dict[str, Any]],
        *,
        batch_size: int = 1000,
        sleep_ms_between_batches: int = 0,
        max_retries: int = 3,
        reporter: Reporter | None = None,
        revision: str | None = None,
        direction: Direction = "upgrade",
        environment: str | None = None,
        lock: MigrationLock | None = None,
        recorder: Recorder | None = None,
    ) -> None:
        self._db = db
        self.batch_size = batch_size
        self.sleep_ms_between_batches = sleep_ms_between_batches
        self.max_retries = max_retries
        self.reporter = reporter or Reporter()
        self.revision = revision
        self.direction: Direction = direction
        self.environment = environment
        self._lock = lock
        self.recorder = recorder
        self._ops: Operations | None = None
        self.current: OperationState | None = None

    @property
    def ops(self) -> Operations:
        if self._ops is None:
            from mongomig.migrations.ops import Operations

            self._ops = Operations(self)
        return self._ops

    @property
    def dry_run(self) -> bool:
        """True while ``plan`` / ``--dry-run`` records this migration instead of running it."""
        return self.recorder is not None

    def collection(self, name: str) -> Collection[dict[str, Any]]:
        if self.recorder is not None:
            from mongomig.migrations.dryrun import RecordingCollection

            # Duck-typed stand-in: reads hit MongoDB, writes are only recorded.
            return RecordingCollection(self._db[name], self.recorder)  # type: ignore[return-value]
        return self._db[name]

    @property
    def unsafe_db(self) -> Database[dict[str, Any]]:
        if self.recorder is not None:
            from mongomig.migrations.dryrun import DryRunUnavailable

            raise DryRunUnavailable("the migration uses ctx.unsafe_db")
        return self._db

    @property
    def db(self) -> Database[dict[str, Any]]:
        """The real database, for ``ctx.ops`` internals (reads only in dry-run mode)."""
        return self._db

    @property
    def database_name(self) -> str:
        return self._db.name

    def log(self, message: str) -> None:
        self.reporter.log(message)

    def check_lock(self) -> None:
        """Raise if the migration lock was lost. Called between batches by ``ctx.ops``."""
        if self._lock is not None:
            self._lock.check()
