"""``ctx.ops``: high-level migration operations.

Every operation is:

- **idempotent where MongoDB allows it**: re-running a half-finished migration is safe
  (existing indexes/collections are skipped, backfills only touch documents still matching
  their filter);
- **batched** for data changes, walking ``_id`` order so memory stays flat, with retries on
  transient errors and optional sleeps to protect production load;
- **reported** through the context's reporter, so the CLI can show progress;
- **simulated** in dry-run mode (``mongomig plan`` / ``--dry-run``): recorded with document
  estimates instead of executed.

Destructive operations can keep a backup: ``unset_field(..., backup=True)`` copies values to
``__mongomig_backup_<revision>``; ``drop_collection(..., backup=True)`` renames the collection
to ``__mongomig_backup_<revision>_<name>``. ``restore_field`` / ``restore_collection`` undo
them (typically in ``downgrade``).

These are what ``revision --autogenerate`` emits. For anything else, use
``ctx.collection(name)`` (plain PyMongo).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any, TypeVar

from mongomig.migrations.context import MigrationContext, OperationState
from mongomig.migrations.dryrun import RecordedOp
from mongomig.schema.indexes import IndexKeys, default_index_name, normalize_index_keys

if TYPE_CHECKING:
    from pymongo.collection import Collection

T = TypeVar("T")

# MongoDB error codes we treat specially.
_NAMESPACE_NOT_FOUND = 26
_INDEX_NOT_FOUND = 27

BACKUP_PREFIX = "__mongomig_backup_"


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
        exists = self._collection_exists(name)
        if self._record(name, "create_collection", "already exists" if exists else ""):
            return
        with self._op("create_collection", name):
            if exists:
                self.ctx.log(f"collection {name} already exists, skipping")
                return
            if validator is not None:
                options.update(
                    validator=dict(validator),
                    validationLevel=validation_level,
                    validationAction=validation_action,
                )
            self.ctx.db.create_collection(name, **options)
            self.ctx.log(f"created collection {name}")

    def drop_collection(self, name: str, *, backup: bool = False) -> None:
        """Drop a collection and all its data.

        With ``backup=True`` the collection is renamed to ``__mongomig_backup_<rev>_<name>``
        instead (instant, restorable with ``restore_collection``).
        """
        target = self._collection_backup_name(name)
        if self._record(
            name,
            "drop_collection",
            f"backup → {target}" if backup else "all data deleted",
            docs=self.ctx.db[name].estimated_document_count(),
            destructive=not backup,
        ):
            return
        with self._op("drop_collection", name):
            if not backup:
                self.ctx.db.drop_collection(name)
                self.ctx.log(f"dropped collection {name}")
                return
            if not self._collection_exists(name):
                self.ctx.log(f"collection {name} does not exist, skipping")
                return
            self.ctx.db[name].rename(target)
            self.ctx.log(f"moved collection {name} → {target} (backup)")

    def restore_collection(self, name: str) -> None:
        """Undo ``drop_collection(name, backup=True)`` from the same revision."""
        source = self._collection_backup_name(name)
        if self._record(name, "restore_collection", f"← {source}"):
            return
        with self._op("restore_collection", name):
            if not self._collection_exists(source):
                self.ctx.log(f"no backup {source} found, skipping")
                return
            self.ctx.db[source].rename(name)
            self.ctx.log(f"restored collection {name} from {source}")

    def rename_collection(self, old: str, new: str, *, drop_target: bool = False) -> None:
        if self._record(old, "rename_collection", f"→ {new}", destructive=drop_target):
            return
        with self._op("rename_collection", old):
            self.ctx.db[old].rename(new, dropTarget=drop_target)
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
        if self.ctx.dry_run:
            self._record_create_index(collection, key_list, options)
            return str(options.get("name") or default_index_name(key_list))
        with self._op("create_index", collection):
            created = self.ctx.db[collection].create_index(key_list, **options)
            self.ctx.log(f"index {collection}.{created} ready")
            return created

    def drop_index(self, collection: str, name: str) -> None:
        """Drop an index by name; skipped if it doesn't exist."""
        from pymongo.errors import OperationFailure

        if self._record(collection, "drop_index", name):
            return
        with self._op("drop_index", collection):
            try:
                self.ctx.db[collection].drop_index(name)
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
        if self.ctx.dry_run:
            self._record_set_validator(collection, validator, level, action)
            return
        with self._op("set_validator", collection):
            if not self._collection_exists(collection):
                self.ctx.db.create_collection(
                    collection,
                    validator=dict(validator),
                    validationLevel=level,
                    validationAction=action,
                )
            else:
                self.ctx.db.command(
                    "collMod",
                    collection,
                    validator=dict(validator),
                    validationLevel=level,
                    validationAction=action,
                )
            self.ctx.log(f"validator set on {collection} (level={level}, action={action})")

    def remove_validator(self, collection: str) -> None:
        if self._record(collection, "remove_validator", ""):
            return
        with self._op("remove_validator", collection):
            if not self._collection_exists(collection):
                self.ctx.log(f"collection {collection} does not exist, skipping")
                return
            self.ctx.db.command("collMod", collection, validator={}, validationLevel="off")
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
        backup: bool = False,
    ) -> BatchResult:
        """Remove ``field`` from documents.

        Without ``backup`` the values are gone for good. With ``backup=True`` each value is
        first copied to ``__mongomig_backup_<revision>``; ``restore_field`` puts them back.
        """
        query = _and(filter, {field: {"$exists": True}})
        before = partial(self._backup_values, collection, field) if backup else None
        return self._batched_update(
            "unset_field",
            collection,
            query,
            {"$unset": {field: ""}},
            batch_size=batch_size,
            destructive=not backup,
            before_batch=before,
        )

    def restore_field(
        self, collection: str, field: str, *, batch_size: int | None = None
    ) -> BatchResult:
        """Put back values saved by ``unset_field(collection, field, backup=True)``."""
        from pymongo import UpdateOne

        store = self.ctx.db[self._backup_name()]
        selector = {"_id.c": collection, "_id.f": field}
        if self._record(
            collection,
            "restore_field",
            f"{field} ← {store.name}",
            docs=self._count(store, selector),
        ):
            return BatchResult(0, 0, 0)
        size = batch_size or self.ctx.batch_size
        task = f"restore_field {collection}"
        restored = batches = 0
        with self._op("restore_field", collection) as state:
            total = self._count(store, selector)
            self.ctx.reporter.progress(task, 0, total)
            while True:
                self.ctx.check_lock()
                saved = list(store.find(selector, limit=size))
                if not saved:
                    break
                writes = [
                    UpdateOne({"_id": d["_id"]["id"]}, {"$set": {field: d["value"]}}) for d in saved
                ]
                result = self._retry(partial(self.ctx.db[collection].bulk_write, writes))
                restored += result.modified_count
                store.delete_many({"_id": {"$in": [d["_id"] for d in saved]}})
                batches += 1
                state.processed += len(saved)
                self.ctx.reporter.progress(task, state.processed, total)
            self.ctx.reporter.progress_done(task)
            self.ctx.log(f"{task}: {restored:,} values restored")
        return BatchResult(matched=restored, modified=restored, batches=batches)

    def rename_field(
        self,
        collection: str,
        old: str,
        new: str,
        *,
        filter: Mapping[str, Any] | None = None,
        batch_size: int | None = None,
    ) -> BatchResult:
        query = _and(filter, {old: {"$exists": True}})
        return self._batched_update(
            "rename_field", collection, query, {"$rename": {old: new}}, batch_size=batch_size
        )

    # --- internals -----------------------------------------------------------------------

    def _collection_exists(self, name: str) -> bool:
        return name in self.ctx.db.list_collection_names(filter={"name": name})

    def _op(self, operation: str, collection: str | None) -> _OpScope:
        return _OpScope(self.ctx, OperationState(operation=operation, collection=collection))

    def _backup_name(self) -> str:
        return f"{BACKUP_PREFIX}{self.ctx.revision or 'adhoc'}"

    def _collection_backup_name(self, name: str) -> str:
        return f"{self._backup_name()}_{name}"

    def _backup_values(self, collection: str, field: str, ids: list[Any]) -> None:
        from pymongo import ReplaceOne

        docs = self.ctx.db[collection].find(
            {"_id": {"$in": ids}, field: {"$exists": True}}, {field: 1}
        )
        writes = []
        for doc in docs:
            key = {"c": collection, "f": field, "id": doc["_id"]}
            writes.append(
                ReplaceOne({"_id": key}, {"_id": key, "value": _get_path(doc, field)}, upsert=True)
            )
        if writes:
            self._retry(partial(self.ctx.db[self._backup_name()].bulk_write, writes))

    # --- dry-run recording ---------------------------------------------------------------

    def _record(
        self,
        collection: str,
        operation: str,
        detail: str,
        *,
        docs: int | None = None,
        destructive: bool = False,
    ) -> bool:
        """In dry-run mode, record the operation and return True (caller skips execution)."""
        if self.ctx.recorder is None:
            return False
        self.ctx.recorder.record(
            RecordedOp(collection, operation, detail, estimated_docs=docs, destructive=destructive)
        )
        return True

    def _record_create_index(
        self, collection: str, keys: list[tuple[str, Any]], options: dict[str, Any]
    ) -> None:
        from mongomig.safety.impact import find_duplicates

        assert self.ctx.recorder is not None
        coll = self.ctx.db[collection]
        name = options.get("name") or default_index_name(keys)
        existing = coll.index_information() if self._collection_exists(collection) else {}
        op = RecordedOp(
            collection,
            "create_index",
            f"{name}{' (unique)' if options.get('unique') else ''}",
            estimated_docs=coll.estimated_document_count(),
        )
        if name in existing:
            op.detail += ", already exists"
        elif options.get("unique"):
            dup, example = find_duplicates(
                coll,
                keys,
                sparse=bool(options.get("sparse")),
                partial=options.get("partialFilterExpression"),
            )
            if dup:
                op.warnings.append(f"will fail: duplicate values exist, e.g. {example}")
            elif dup is None:
                op.warnings.append("duplicate check timed out; the build may fail")
        self.ctx.recorder.record(op)

    def _record_set_validator(
        self, collection: str, validator: Mapping[str, Any], level: str, action: str
    ) -> None:
        assert self.ctx.recorder is not None
        op = RecordedOp(collection, "set_validator", f"level={level}, action={action}")
        if self._collection_exists(collection):
            failing = self._count(self.ctx.db[collection], {"$nor": [dict(validator)]})
            if failing:
                op.warnings.append(
                    f"~{failing:,} existing documents don't match the validator"
                    + (" (writes to them will be rejected)" if level == "strict" else "")
                )
        self.ctx.recorder.record(op)

    def _count(self, coll: Collection[dict[str, Any]], query: Mapping[str, Any]) -> int:
        return self._estimate(coll, dict(query)) or 0

    def _batched_update(
        self,
        operation: str,
        collection: str,
        query: dict[str, Any],
        update: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        batch_size: int | None,
        destructive: bool = False,
        before_batch: Callable[[list[Any]], None] | None = None,
    ) -> BatchResult:
        coll = self.ctx.db[collection]
        size = batch_size or self.ctx.batch_size
        if size <= 0:
            raise ValueError("batch_size must be positive")
        update_doc: Any = (
            dict(update) if isinstance(update, Mapping) else [dict(stage) for stage in update]
        )
        if self.ctx.recorder is not None:
            from mongomig.migrations.dryrun import update_summary
            from mongomig.safety.impact import uses_collection_scan

            detail = update_summary(update_doc)
            if before_batch is not None:
                detail += f" (backup → {self._backup_name()})"
            self.ctx.recorder.record(
                RecordedOp(
                    collection,
                    operation,
                    detail,
                    estimated_docs=self._estimate(coll, query),
                    collection_scan=uses_collection_scan(coll, query),
                    destructive=destructive,
                )
            )
            return BatchResult(matched=0, modified=0, batches=0)
        task = f"{operation} {collection}"
        sleep_s = self.ctx.sleep_ms_between_batches / 1000

        with self._op(operation, collection) as state:
            total = self._estimate(coll, query)
            self.ctx.reporter.progress(task, 0, total)
            matched = modified = 0
            for ids in self._id_batches(coll, query, size):
                self.ctx.check_lock()
                if before_batch is not None:
                    before_batch(ids)
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


def _and(user_filter: Mapping[str, Any] | None, condition: dict[str, Any]) -> dict[str, Any]:
    """Combine without letting one side overwrite a key of the other (``{"a": 0}`` and
    ``{"a": {"$exists": True}}`` must both apply)."""
    if not user_filter:
        return condition
    return {"$and": [dict(user_filter), condition]}


def _get_path(doc: Mapping[str, Any], path: str) -> Any:
    value: Any = doc
    for part in path.split("."):
        if not isinstance(value, Mapping):
            return None
        value = value.get(part)
    return value


def _is_transient(exc: BaseException) -> bool:
    from pymongo.errors import AutoReconnect, PyMongoError

    if isinstance(exc, AutoReconnect):  # includes NotPrimaryError, NetworkTimeout
        return True
    return isinstance(exc, PyMongoError) and exc.has_error_label("RetryableWriteError")
