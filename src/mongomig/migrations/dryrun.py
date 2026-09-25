"""Dry-run support: record what a migration *would* do, without writing anything.

In dry-run mode:

- ``ctx.ops`` operations record themselves with document estimates (exact about *what*,
  estimated about *how many*);
- ``ctx.collection(name)`` returns a ``RecordingCollection``: reads go to MongoDB, writes are
  recorded and return stand-in results;
- ``ctx.unsafe_db`` raises ``DryRunUnavailable``: raw database access can't be simulated.

Migration code runs for real in dry-run mode (only its database writes are intercepted), so
migrations shouldn't have side effects outside ``ctx``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pymongo.collection import Collection


class DryRunUnavailable(Exception):
    """The migration used something a dry run can't simulate (e.g. ``ctx.unsafe_db``)."""


@dataclass
class RecordedOp:
    collection: str
    operation: str  # "backfill", "create_index", "update_one", ...
    detail: str = ""  # human summary: "$set status", "users_email_unique (unique)"
    estimated_docs: int | None = None  # documents touched, when it can be estimated
    collection_scan: bool | None = None  # filter not served by an index
    destructive: bool = False
    exact: bool = True  # False for raw collection writes (custom code)
    calls: int = 1  # merged repeated calls, e.g. update_one in a loop
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "collection": self.collection,
            "operation": self.operation,
            "detail": self.detail,
            "estimated_docs": self.estimated_docs,
            "collection_scan": self.collection_scan,
            "destructive": self.destructive,
            "exact": self.exact,
            "calls": self.calls,
            "warnings": self.warnings,
        }


class Recorder:
    def __init__(self) -> None:
        self.ops: list[RecordedOp] = []

    def record(self, op: RecordedOp) -> RecordedOp:
        last = self.ops[-1] if self.ops else None
        # Merge per-document loops (update_one called N times) into one entry.
        if (
            last is not None
            and not op.exact
            and not last.exact
            and (last.collection, last.operation) == (op.collection, op.operation)
        ):
            last.calls += 1
            if op.estimated_docs is not None:
                last.estimated_docs = (last.estimated_docs or 0) + op.estimated_docs
            return last
        self.ops.append(op)
        return op


# --- stand-in results for recorded raw writes ---------------------------------------------


@dataclass(frozen=True)
class _WriteResult:
    matched_count: int = 0
    modified_count: int = 0
    deleted_count: int = 0
    inserted_id: Any = None
    inserted_ids: tuple[Any, ...] = ()
    upserted_id: Any = None
    acknowledged: bool = False  # signals "nothing was written"


_READS = frozenset(
    {
        "find",
        "find_one",
        "find_raw_batches",
        "count_documents",
        "estimated_document_count",
        "distinct",
        "list_indexes",
        "index_information",
        "options",
        "name",
        "full_name",
        "codec_options",
        "read_preference",
        "write_concern",
        "read_concern",
        "list_search_indexes",
    }
)
_WRITE_STAGES = ("$out", "$merge")


class RecordingCollection:
    """Proxy for a PyMongo ``Collection`` that records writes instead of executing them."""

    def __init__(self, collection: Collection[dict[str, Any]], recorder: Recorder) -> None:
        self._coll = collection
        self._recorder = recorder

    def __getattr__(self, name: str) -> Any:
        if name in _READS:
            return getattr(self._coll, name)
        raise DryRunUnavailable(f"Collection.{name}() can't be simulated in a dry run")

    def __getitem__(self, name: str) -> RecordingCollection:
        return RecordingCollection(self._coll[name], self._recorder)

    def with_options(self, **kwargs: Any) -> RecordingCollection:
        return RecordingCollection(self._coll.with_options(**kwargs), self._recorder)

    # --- reads that may write -------------------------------------------------------------

    def aggregate(self, pipeline: list[Mapping[str, Any]], *args: Any, **kwargs: Any) -> Any:
        if any(stage_name in stage for stage in pipeline for stage_name in _WRITE_STAGES):
            self._write("aggregate", "pipeline with $out/$merge", None)
            return iter(())
        return self._coll.aggregate(pipeline, *args, **kwargs)

    # --- writes ---------------------------------------------------------------------------

    def insert_one(self, document: Any, *args: Any, **kwargs: Any) -> _WriteResult:
        self._write("insert_one", "", 1)
        return _WriteResult(inserted_id=getattr(document, "get", lambda _k: None)("_id"))

    def insert_many(self, documents: Any, *args: Any, **kwargs: Any) -> _WriteResult:
        docs = list(documents)
        self._write("insert_many", "", len(docs))
        return _WriteResult(inserted_ids=tuple(d.get("_id") for d in docs))

    def update_one(self, filter: Mapping[str, Any], update: Any, *a: Any, **k: Any) -> _WriteResult:
        matched = min(self._count(filter, limit=1), 1)
        self._write("update_one", update_summary(update), matched)
        return _WriteResult(matched_count=matched)

    def update_many(
        self, filter: Mapping[str, Any], update: Any, *a: Any, **k: Any
    ) -> _WriteResult:
        matched = self._count(filter)
        self._write("update_many", update_summary(update), matched, filter)
        return _WriteResult(matched_count=matched)

    def replace_one(
        self, filter: Mapping[str, Any], replacement: Any, *a: Any, **k: Any
    ) -> _WriteResult:
        matched = min(self._count(filter, limit=1), 1)
        self._write("replace_one", "", matched)
        return _WriteResult(matched_count=matched)

    def delete_one(self, filter: Mapping[str, Any], *a: Any, **k: Any) -> _WriteResult:
        matched = min(self._count(filter, limit=1), 1)
        self._write("delete_one", "", matched, destructive=True)
        return _WriteResult()

    def delete_many(self, filter: Mapping[str, Any], *a: Any, **k: Any) -> _WriteResult:
        matched = self._count(filter)
        self._write("delete_many", "", matched, filter, destructive=True)
        return _WriteResult()

    def bulk_write(self, requests: Any, *a: Any, **k: Any) -> _WriteResult:
        ops = list(requests)
        self._write("bulk_write", f"{len(ops)} operations", len(ops))
        return _WriteResult()

    def find_one_and_update(self, filter: Mapping[str, Any], *a: Any, **k: Any) -> Any:
        self._write("find_one_and_update", "", min(self._count(filter, limit=1), 1))
        return self._coll.find_one(filter)

    def find_one_and_replace(self, filter: Mapping[str, Any], *a: Any, **k: Any) -> Any:
        self._write("find_one_and_replace", "", min(self._count(filter, limit=1), 1))
        return self._coll.find_one(filter)

    def find_one_and_delete(self, filter: Mapping[str, Any], *a: Any, **k: Any) -> Any:
        self._write("find_one_and_delete", "", 1, destructive=True)
        return self._coll.find_one(filter)

    def create_index(self, keys: Any, **kwargs: Any) -> str:
        from mongomig.schema.indexes import default_index_name, normalize_index_keys

        name = kwargs.get("name") or default_index_name(normalize_index_keys(keys))
        self._write("create_index", name, None)
        return str(name)

    def create_indexes(self, indexes: Any, *a: Any, **k: Any) -> list[str]:
        models = list(indexes)
        self._write("create_indexes", f"{len(models)} indexes", None)
        return [m.document.get("name", "") for m in models]

    def drop_index(self, index_or_name: Any, *a: Any, **k: Any) -> None:
        self._write("drop_index", str(index_or_name), None)

    def drop_indexes(self, *a: Any, **k: Any) -> None:
        self._write("drop_indexes", "all indexes", None)

    def drop(self, *a: Any, **k: Any) -> None:
        self._write("drop", "entire collection", self._estimated(), destructive=True)

    def rename(self, new_name: str, *a: Any, **k: Any) -> None:
        self._write("rename", f"→ {new_name}", None)

    # --- internals ------------------------------------------------------------------------

    def _write(
        self,
        operation: str,
        detail: str,
        docs: int | None,
        filter: Mapping[str, Any] | None = None,
        *,
        destructive: bool = False,
    ) -> None:
        from mongomig.safety.impact import uses_collection_scan

        scan = uses_collection_scan(self._coll, filter) if filter is not None else None
        self._recorder.record(
            RecordedOp(
                collection=self._coll.name,
                operation=operation,
                detail=detail,
                estimated_docs=docs,
                collection_scan=scan,
                destructive=destructive,
                exact=False,
            )
        )

    def _count(self, filter: Mapping[str, Any], limit: int = 0) -> int:
        from pymongo.errors import PyMongoError

        try:
            kwargs: dict[str, Any] = {"maxTimeMS": 5000}
            if limit:
                kwargs["limit"] = limit
            return self._coll.count_documents(dict(filter), **kwargs)
        except PyMongoError:
            return 0

    def _estimated(self) -> int:
        return self._coll.estimated_document_count()


def update_summary(update: Any) -> str:
    if isinstance(update, Mapping):
        parts = []
        for operator, spec in update.items():
            fields = ", ".join(spec) if isinstance(spec, Mapping) else ""
            parts.append(f"{operator} {fields}".strip())
        return "; ".join(parts)
    if isinstance(update, list):
        return "pipeline update"
    return ""
