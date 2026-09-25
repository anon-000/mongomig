"""``ctx.ops``: high-level migration operations.

Every operation is:

- **idempotent where MongoDB allows it**: re-running a half-finished migration is safe
  (existing indexes/collections are skipped, backfills only touch documents still matching
  their filter);
- **batched** for data changes, walking ``_id`` order so memory stays flat, with retries on
  transient errors and optional sleeps to protect production load;
- **reported** through the context's reporter, so the CLI can show progress.

These are what ``revision --autogenerate`` will emit. For anything else, use
``ctx.collection(name)`` (plain PyMongo).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any, TypeVar

from mongomig.migrations.context import MigrationContext, OperationState
from mongomig.schema.indexes import IndexKeys, normalize_index_keys

if TYPE_CHECKING:
    from pymongo.collection import Collection

T = TypeVar("T")

# MongoDB error codes we treat specially.
_NAMESPACE_NOT_FOUND = 26
_INDEX_NOT_FOUND = 27


@dataclass(frozen=True)
class BatchResult:
    matched: int
    modified: int
    batches: int


class Operations:
    def __init__(self, ctx: MigrationContext) -> None:
        self.ctx = ctx

    # --- collections ---------------------------------------------------------------------

    def create_collection(
        self,
        name: str,
        *,
        validator: Mapping[str, Any] | None = None,
        validation_level: str = "moderate",
        validation_action: str = "error",
        **options: Any,
    ) -> None:
        """Create a collection; skipped if it already exists."""
        db = self.ctx.unsafe_db
        with self._op("create_collection", name):
            if self._collection_exists(name):
                self.ctx.log(f"collection {name} already exists, skipping")
                return
            if validator is not None:
                options.update(
                    validator=dict(validator),
                    validationLevel=validation_level,
                    validationAction=validation_action,
                )
            db.create_collection(name, **options)
            self.ctx.log(f"created collection {name}")

    def drop_collection(self, name: str) -> None:
        """Drop a collection and all its data. Irreversible."""
        with self._op("drop_collection", name):
            self.ctx.unsafe_db.drop_collection(name)
            self.ctx.log(f"dropped collection {name}")

    def rename_collection(self, old: str, new: str, *, drop_target: bool = False) -> None:
        with self._op("rename_collection", old):
            self.ctx.collection(old).rename(new, dropTarget=drop_target)
            self.ctx.log(f"renamed collection {old} → {new}")

    # --- indexes -------------------------------------------------------------------------

    def create_index(
        self, collection: str, keys: IndexKeys, *, name: str | None = None, **options: Any
    ) -> str:
        """Create an index. A no-op if an identical index exists.

        ``options`` are passed to PyMongo: ``unique``, ``sparse``, ``partialFilterExpression``,
        ``expireAfterSeconds``, ``collation``, ``hidden``, ...
        """
        key_list = normalize_index_keys(keys)
        if name is not None:
            options["name"] = name
        with self._op("create_index", collection):
            created = self.ctx.collection(collection).create_index(key_list, **options)
            self.ctx.log(f"index {collection}.{created} ready")
            return created

    def drop_index(self, collection: str, name: str) -> None:
        """Drop an index by name; skipped if it doesn't exist."""
        from pymongo.errors import OperationFailure

        with self._op("drop_index", collection):
            try:
                self.ctx.collection(collection).drop_index(name)
            except OperationFailure as exc:
                if exc.code in (_INDEX_NOT_FOUND, _NAMESPACE_NOT_FOUND):
                    self.ctx.log(f"index {collection}.{name} does not exist, skipping")
                    return
                raise
            self.ctx.log(f"dropped index {collection}.{name}")

    # --- validators ----------------------------------------------------------------------

    def set_validator(
        self,
        collection: str,
        validator: Mapping[str, Any],
        *,
        level: str = "moderate",
        action: str = "error",
    ) -> None:
        """Set a collection validator (creates the collection if needed).

        ``level="moderate"`` (default) only validates inserts and updates to documents that
        already pass, so existing non-conforming documents don't block writes.
        """
        with self._op("set_validator", collection):
            if not self._collection_exists(collection):
                self.ctx.unsafe_db.create_collection(
                    collection,
                    validator=dict(validator),
                    validationLevel=level,
                    validationAction=action,
                )
            else:
                self.ctx.unsafe_db.command(
                    "collMod",
                    collection,
                    validator=dict(validator),
                    validationLevel=level,
                    validationAction=action,
                )
            self.ctx.log(f"validator set on {collection} (level={level}, action={action})")

    def remove_validator(self, collection: str) -> None:
        with self._op("remove_validator", collection):
            if not self._collection_exists(collection):
                self.ctx.log(f"collection {collection} does not exist, skipping")
                return
            self.ctx.unsafe_db.command("collMod", collection, validator={}, validationLevel="off")
            self.ctx.log(f"validator removed from {collection}")

    # --- data ----------------------------------------------------------------------------

    def backfill(
        self,
        collection: str,
        filter: Mapping[str, Any],
        update: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        batch_size: int | None = None,
    ) -> BatchResult:
        """Apply ``update`` to every document matching ``filter``, in ``_id``-ordered batches.

        Write the filter so already-migrated documents no longer match it (e.g.
        ``{"status": {"$exists": False}}`` for ``{"$set": {"status": "active"}}``); then an
        interrupted backfill can simply be re-run. ``update`` may be an update document or an
        aggregation pipeline.
        """
        return self._batched_update(
            "backfill", collection, dict(filter), update, batch_size=batch_size
        )

    def unset_field(
        self,
        collection: str,
        field: str,
        *,
        filter: Mapping[str, Any] | None = None,
        batch_size: int | None = None,
    ) -> BatchResult:
        """Remove ``field`` from documents. The removed values are gone for good."""
        query = {**(filter or {}), field: {"$exists": True}}
        return self._batched_update(
            "unset_field", collection, query, {"$unset": {field: ""}}, batch_size=batch_size
        )

    def rename_field(
        self,
        collection: str,
        old: str,
        new: str,
        *,
        filter: Mapping[str, Any] | None = None,
        batch_size: int | None = None,
    ) -> BatchResult:
        query = {**(filter or {}), old: {"$exists": True}}
        return self._batched_update(
            "rename_field", collection, query, {"$rename": {old: new}}, batch_size=batch_size
        )

    # --- internals -----------------------------------------------------------------------

    def _collection_exists(self, name: str) -> bool:
        return name in self.ctx.unsafe_db.list_collection_names(filter={"name": name})

    def _op(self, operation: str, collection: str | None) -> _OpScope:
        return _OpScope(self.ctx, OperationState(operation=operation, collection=collection))

    def _batched_update(
        self,
        operation: str,
        collection: str,
        query: dict[str, Any],
        update: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        batch_size: int | None,
    ) -> BatchResult:
        coll = self.ctx.collection(collection)
        size = batch_size or self.ctx.batch_size
        if size <= 0:
            raise ValueError("batch_size must be positive")
        update_doc: Any = (
            dict(update) if isinstance(update, Mapping) else [dict(stage) for stage in update]
        )
        task = f"{operation} {collection}"
        sleep_s = self.ctx.sleep_ms_between_batches / 1000

        with self._op(operation, collection) as state:
            total = self._estimate(coll, query)
            self.ctx.reporter.progress(task, 0, total)
            matched = modified = 0
            for ids in self._id_batches(coll, query, size):
                self.ctx.check_lock()
                batch_query = {"$and": [query, {"_id": {"$in": ids}}]}
                result = self._retry(partial(coll.update_many, batch_query, update_doc))
                matched += result.matched_count
                modified += result.modified_count
                state.batch += 1
                state.processed += len(ids)
                self.ctx.reporter.progress(task, state.processed, total)
                if sleep_s:
                    time.sleep(sleep_s)
            self.ctx.reporter.progress_done(task)
            self.ctx.log(
                f"{task}: {modified:,} modified ({matched:,} matched, {state.batch} batches)"
            )
            return BatchResult(matched=matched, modified=modified, batches=state.batch)

    def _estimate(self, coll: Collection[dict[str, Any]], query: dict[str, Any]) -> int | None:
        """Best-effort document count for progress; ``None`` if it would be too slow."""
        from pymongo.errors import PyMongoError

        try:
            return coll.count_documents(query, maxTimeMS=5000)
        except PyMongoError:
            return None

    def _id_batches(
        self, coll: Collection[dict[str, Any]], query: dict[str, Any], size: int
    ) -> Iterator[list[Any]]:
        """Yield lists of ``_id`` values matching ``query`` in ascending ``_id`` order.

        A single cursor is used (so mixed ``_id`` types are all visited); if it dies from a
        transient error it is reopened after the last ``_id`` seen.
        """
        last_id: Any = None
        started = False
        attempts = 0
        while True:
            q = query if not started else {"$and": [query, {"_id": {"$gt": last_id}}]}
            cursor = coll.find(q, {"_id": 1}, sort=[("_id", 1)], batch_size=size)
            batch: list[Any] = []
            try:
                for doc in cursor:
                    batch.append(doc["_id"])
                    if len(batch) >= size:
                        last_id, started, attempts = batch[-1], True, 0
                        yield batch
                        batch = []
                if batch:
                    yield batch
                return
            except Exception as exc:
                attempts += 1
                if not _is_transient(exc) or attempts > self.ctx.max_retries:
                    raise
                if batch:
                    last_id, started = batch[-1], True
                    yield batch
                self._backoff(attempts, exc)
            finally:
                cursor.close()

    def _retry(self, fn: Callable[[], T]) -> T:
        attempts = 0
        while True:
            try:
                return fn()
            except Exception as exc:
                attempts += 1
                if not _is_transient(exc) or attempts > self.ctx.max_retries:
                    raise
                self._backoff(attempts, exc)

    def _backoff(self, attempt: int, exc: BaseException) -> None:
        delay = min(0.5 * 2 ** (attempt - 1), 10.0)
        self.ctx.reporter.warn(
            f"transient error ({type(exc).__name__}), retry {attempt}/{self.ctx.max_retries} "
            f"in {delay:.1f}s"
        )
        time.sleep(delay)


class _OpScope:
    """Sets ``ctx.current`` for the duration of an operation (for error reporting)."""

    def __init__(self, ctx: MigrationContext, state: OperationState) -> None:
        self.ctx = ctx
        self.state = state

    def __enter__(self) -> OperationState:
        self.ctx.current = self.state
        return self.state

    def __exit__(self, exc_type: object, *_: object) -> None:
        if exc_type is None:
            self.ctx.current = None
        # On error ctx.current is kept so the executor can report where it failed.


def _is_transient(exc: BaseException) -> bool:
    from pymongo.errors import AutoReconnect, PyMongoError

    if isinstance(exc, AutoReconnect):  # includes NotPrimaryError, NetworkTimeout
        return True
    return isinstance(exc, PyMongoError) and exc.has_error_label("RetryableWriteError")
