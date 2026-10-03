"""``ctx``: what every ``upgrade(ctx)`` / ``downgrade(ctx)`` receives."""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from mongomig.migrations.reporting import Direction, Reporter

if TYPE_CHECKING:
    from pymongo import MongoClient
    from pymongo.client_session import ClientSession
    from pymongo.collection import Collection
    from pymongo.database import Database

    from mongomig.migrations.checkpoints import Checkpoint, CheckpointStore
    from mongomig.migrations.dryrun import Recorder
    from mongomig.migrations.lock import MigrationLock
    from mongomig.migrations.ops import Operations


class Batch(list[dict[str, Any]]):
    """One batch of documents from ``ctx.batches``.

    ``session`` is set when the loop is ``transactional=True``: pass it to every write
    (``coll.update_one(..., session=batch.session)``) so the writes commit together with the
    checkpoint.
    """

    def __init__(
        self, docs: list[dict[str, Any]], *, number: int, session: ClientSession | None = None
    ) -> None:
        super().__init__(docs)
        self.number = number
        self.session = session


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
        checkpoints: CheckpointStore | None = None,
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
        self.checkpoints = checkpoints
        self._checkpoint_seq = 0
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

    def next_checkpoint_key(self, kind: str, collection: str) -> str:
        """Position-based key for the next batched loop (``1:backfill:users``)."""
        self._checkpoint_seq += 1
        return f"{self._checkpoint_seq}:{kind}:{collection}"

    # --- transactions --------------------------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[ClientSession | None]:
        """Run writes atomically: ``with ctx.transaction() as s: coll.update_one(..., session=s)``.

        Commits when the block ends, aborts if it raises. Needs a replica set or sharded
        cluster. In dry-run mode the session is ``None`` (writes are only recorded anyway).
        """
        if self.dry_run:
            yield None
            return
        client = self._db.client
        require_transactions(client)
        with client.start_session() as session, session.start_transaction():
            yield session

    # --- resumable loops -----------------------------------------------------------------

    def batches(
        self,
        collection: str,
        filter: Mapping[str, Any] | None = None,
        *,
        projection: Mapping[str, Any] | None = None,
        batch_size: int | None = None,
        transactional: bool = False,
    ) -> Iterator[Batch]:
        """Iterate ``collection`` in ``_id`` order, ``batch_size`` documents at a time.

        Progress is checkpointed after each batch; if the migration fails, the next
        ``upgrade`` continues after the last completed batch instead of starting over.

        - default: a batch interrupted mid-way is processed again on the next run
          (at-least-once), so make per-document work repeatable;
        - ``transactional=True``: each batch runs in a transaction together with its checkpoint
          (exactly-once). Pass ``batch.session`` to every write. Needs a replica set.

        Leave the loop early with ``break`` and the rest is not processed (or checkpointed).
        """
        from mongomig.migrations.batching import START, id_ordered_batches
        from mongomig.migrations.checkpoints import fingerprint
        from mongomig.migrations.ops import _is_transient

        query = dict(filter or {})
        size = batch_size or self.batch_size
        if size <= 0:
            raise ValueError("batch_size must be positive")
        key = self.next_checkpoint_key("batches", collection)
        coll = self._db[collection]

        if self.recorder is not None:
            # Dry run: record the scope, run the body on the first batch only (its writes are
            # recorded), so plan stays fast on big collections.
            self._record_batches(coll, query)
            first = next(id_ordered_batches(coll, query, size, projection=projection), None)
            if first:
                yield Batch(first, number=1)
            return
        if transactional:
            require_transactions(self._db.client)

        store = self.checkpoints
        cp = self._resume_point(store, key, collection, fingerprint(collection, query, projection))
        if cp is None:
            return

        task = f"batches {collection}"
        total = _count(coll, query)
        self.reporter.progress(task, cp.processed, total)
        with _scope(self, "batches", collection) as state:
            state.processed = cp.processed
            for docs in id_ordered_batches(
                coll,
                query,
                size,
                projection=projection,
                start_after=cp.last_id if cp.has_position else START,
                max_retries=self.max_retries,
                is_transient=_is_transient,
            ):
                self.check_lock()
                cp.batches += 1
                state.batch = cp.batches
                if transactional:
                    yield from self._transactional_batch(docs, cp, store)
                else:
                    yield Batch(docs, number=cp.batches)
                    _advance(cp, docs)
                    if store is not None:
                        store.save(cp)
                state.processed = cp.processed
                self.reporter.progress(task, cp.processed, total)
                if self.sleep_ms_between_batches:
                    time.sleep(self.sleep_ms_between_batches / 1000)
            cp.done = True
            if store is not None:
                store.save(cp)
            self.reporter.progress_done(task)
            self.log(f"{task}: {cp.processed:,} documents in {cp.batches} batches")

    def _resume_point(
        self, store: CheckpointStore | None, key: str, collection: str, fp: str
    ) -> Checkpoint | None:
        """Where to start this loop; ``None`` if an earlier run already completed it."""
        from mongomig.migrations.checkpoints import Checkpoint

        cp = store.load(key, fp) if store is not None else None
        if cp is not None and cp.done:
            self.log(f"batches {collection}: already completed in an earlier run, skipping")
            return None
        if cp is None:
            return Checkpoint(key=key, collection=collection, fingerprint=fp)
        if cp.processed:
            self.log(f"batches {collection}: resuming after {cp.processed:,} documents")
        return cp

    def _transactional_batch(
        self, docs: list[dict[str, Any]], cp: Any, store: CheckpointStore | None
    ) -> Iterator[Batch]:
        with self._db.client.start_session() as session:
            session.start_transaction()
            try:
                yield Batch(docs, number=cp.batches, session=session)
                _advance(cp, docs)
                if store is not None:
                    store.save(cp, session=session)
                _commit(session, self.max_retries)
            except BaseException:
                if session.in_transaction:
                    session.abort_transaction()
                cp.batches -= 1
                raise

    def _record_batches(self, coll: Collection[dict[str, Any]], query: dict[str, Any]) -> None:
        from mongomig.migrations.dryrun import RecordedOp
        from mongomig.safety.impact import uses_collection_scan

        assert self.recorder is not None
        self.recorder.record(
            RecordedOp(
                coll.name,
                "batches",
                "custom loop (first batch simulated)",
                estimated_docs=_count(coll, query),
                collection_scan=uses_collection_scan(coll, query),
                exact=False,
            )
        )

    def log(self, message: str) -> None:
        self.reporter.log(message)

    def check_lock(self) -> None:
        """Raise if the migration lock was lost. Called between batches by ``ctx.ops``."""
        if self._lock is not None:
            self._lock.check()


def require_transactions(client: MongoClient[dict[str, Any]]) -> None:
    """Transactions need a replica set or sharded cluster; fail clearly on a standalone."""
    from mongomig.errors import TransactionsUnsupportedError

    hello = client.admin.command("hello")
    if not (hello.get("setName") or hello.get("msg") == "isdbgrid"):
        raise TransactionsUnsupportedError(
            "Transactions need a replica set or a sharded cluster; this is a standalone server.",
            suggestion="Run MongoDB as a (single-node) replica set, or drop transactional=True / "
            "ctx.transaction() from the migration.",
        )


def _advance(cp: Any, docs: list[dict[str, Any]]) -> None:
    cp.processed += len(docs)
    cp.last_id = docs[-1]["_id"]
    cp.has_position = True


def _commit(session: ClientSession, max_retries: int) -> None:
    """Commit, retrying when the outcome is unknown (safe: commit is idempotent)."""
    from pymongo.errors import PyMongoError

    attempts = 0
    while True:
        try:
            session.commit_transaction()
            return
        except PyMongoError as exc:
            attempts += 1
            if not exc.has_error_label("UnknownTransactionCommitResult") or attempts > max_retries:
                raise


def _count(coll: Collection[dict[str, Any]], query: Mapping[str, Any]) -> int | None:
    from pymongo.errors import PyMongoError

    try:
        return coll.count_documents(dict(query), maxTimeMS=5000)
    except PyMongoError:
        return None


@contextmanager
def _scope(ctx: MigrationContext, operation: str, collection: str) -> Iterator[OperationState]:
    """Like ctx.ops' operation scope: keep ``ctx.current`` for error messages."""
    state = OperationState(operation=operation, collection=collection)
    ctx.current = state
    yield state
    ctx.current = None
