"""Walking a collection in ``_id`` order, in batches, resumably.

Used by ``ctx.ops`` (updates by ``_id`` batches) and ``ctx.batches`` (custom loops).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pymongo.collection import Collection

# MongoDB's sort order across BSON types (comparison/sort order). `_id` can't be an array.
_SORT_GROUPS: tuple[tuple[str, ...], ...] = (
    ("null",),
    ("int", "long", "double", "decimal"),
    ("string", "symbol"),
    ("object",),
    ("binData",),
    ("objectId",),
    ("bool",),
    ("date",),
    ("timestamp",),
    ("regex",),
)


class _Start:
    """Sentinel: no resume point (``None`` is a valid ``_id``)."""


START = _Start()


def after_id(last_id: Any) -> dict[str, Any]:
    """Filter for documents sorting after ``last_id`` in ``_id`` order.

    ``{"_id": {"$gt": x}}`` alone only matches values of the same BSON type, so ids of a type
    that sorts later (e.g. strings after numbers) are added explicitly.
    """
    from mongomig.schema.inference import bson_type_of

    kind = bson_type_of(last_id)
    index = next((i for i, group in enumerate(_SORT_GROUPS) if kind in group), None)
    greater: dict[str, Any] = {"_id": {"$gt": last_id}}
    if index is None:
        return greater
    later = [t for group in _SORT_GROUPS[index + 1 :] for t in group]
    if not later:
        return greater
    return {"$or": [greater, {"_id": {"$type": later}}]}


def id_ordered_batches(
    coll: Collection[dict[str, Any]],
    query: Mapping[str, Any],
    size: int,
    *,
    projection: Mapping[str, Any] | None = None,
    start_after: Any = START,
    max_retries: int = 3,
    is_transient: Callable[[BaseException], bool] | None = None,
    on_retry: Callable[[int, BaseException], None] | None = None,
) -> Iterator[list[dict[str, Any]]]:
    """Yield documents matching ``query`` in ascending ``_id`` order, ``size`` at a time.

    One cursor is used while it lives; if it dies from a transient error it is reopened after
    the last ``_id`` seen. ``start_after`` resumes after a checkpointed ``_id``.
    """
    if projection is not None and projection.get("_id") in (0, False):
        raise ValueError("projection must include _id (batches resume by _id)")
    last: Any = start_after
    attempts = 0
    while True:
        q = dict(query) if last is START else {"$and": [dict(query), after_id(last)]}
        cursor = coll.find(q, projection, sort=[("_id", 1)], batch_size=size)
        batch: list[dict[str, Any]] = []
        try:
            for doc in cursor:
                batch.append(doc)
                if len(batch) >= size:
                    last, attempts = doc["_id"], 0
                    yield batch
                    batch = []
            if batch:
                yield batch
            return
        except Exception as exc:
            attempts += 1
            if is_transient is None or not is_transient(exc) or attempts > max_retries:
                raise
            if batch:
                last = batch[-1]["_id"]
                yield batch
            if on_retry is not None:
                on_retry(attempts, exc)
        finally:
            cursor.close()
