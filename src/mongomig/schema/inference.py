"""Observed schema: walk real documents and record which fields/types occur how often.

The result is always labelled with how it was obtained (sample size, mode) because an
inferred schema is evidence, not truth: documents outside the sample may differ.
"""

from __future__ import annotations

import datetime
import math
import re
import time
import uuid
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

from mongomig.schema.models import CollectionSchema, FieldSchema, FieldStats

if TYPE_CHECKING:
    from pymongo.collection import Collection
    from pymongo.database import Database

SampleMode = Literal["sample", "full", "percent"]

MAX_DEPTH = 20
# Objects with more distinct keys than this are treated as maps ({"<userId>": ...}).
MAX_KEYS_PER_OBJECT = 200
INT32_MIN, INT32_MAX = -(2**31), 2**31 - 1


@dataclass
class _Node:
    count: int = 0
    types: Counter[str] = field(default_factory=Counter)
    children: dict[str, _Node] = field(default_factory=dict)
    items: _Node | None = None
    overflow: bool = False  # too many distinct keys: treated as a map


_BSON_CLASS_TYPES = {
    "ObjectId": "objectId",
    "Decimal128": "decimal",
    "Binary": "binData",
    "Regex": "regex",
    "Timestamp": "timestamp",
    "Code": "javascript",  # a str subclass: must be checked before str
    "MinKey": "minKey",
    "MaxKey": "maxKey",
    "DatetimeMS": "date",
    "DBRef": "object",
    "Int64": "long",  # an int subclass
}


def bson_type_of(value: Any) -> str:  # noqa: PLR0911 (type dispatch)
    """BSON type name (as used by ``$jsonSchema``) of a decoded PyMongo value."""
    if value is None:
        return "null"
    special = _BSON_CLASS_TYPES.get(type(value).__name__)
    if special is not None:
        return special
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int" if INT32_MIN <= value <= INT32_MAX else "long"
    if isinstance(value, float):
        return "double"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, list | tuple):
        return "array"
    if isinstance(value, datetime.datetime):
        return "date"
    if isinstance(value, bytes | uuid.UUID):
        return "binData"
    if isinstance(value, Decimal):
        return "decimal"
    if isinstance(value, re.Pattern):
        return "regex"
    return type(value).__name__


class SchemaAccumulator:
    """Feed documents in, get a ``CollectionSchema`` with stats out."""

    def __init__(self) -> None:
        self.root = _Node()
        self.documents = 0

    def add(self, document: Mapping[str, Any]) -> None:
        self.documents += 1
        self._add_object(self.root, document, depth=0)

    def add_all(self, documents: Iterable[Mapping[str, Any]]) -> None:
        for doc in documents:
            self.add(doc)

    def _add_object(self, node: _Node, obj: Mapping[str, Any], depth: int) -> None:
        if depth >= MAX_DEPTH:
            node.overflow = True
            return
        for key, value in obj.items():
            child = node.children.get(key)
            if child is None:
                if len(node.children) >= MAX_KEYS_PER_OBJECT:
                    node.overflow = True
                    continue
                child = node.children[key] = _Node()
            self._add_value(child, value, depth + 1)

    def _add_value(self, node: _Node, value: Any, depth: int) -> None:
        node.count += 1
        kind = bson_type_of(value)
        node.types[kind] += 1
        if kind == "object":
            self._add_object(node, value, depth)
        elif kind == "array":
            if node.items is None:
                node.items = _Node()
            for element in value:
                self._add_value(node.items, element, depth + 1)

    def result(self, name: str) -> CollectionSchema:
        return CollectionSchema(
            name=name,
            fields=self._fields(self.root, parents=self.documents),
            source="observed",
        )

    def _fields(self, node: _Node, parents: int) -> dict[str, FieldSchema]:
        ordered = sorted(node.children.items(), key=lambda kv: (kv[0] != "_id", -kv[1].count))
        return {key: self._field(child, parents) for key, child in ordered}

    def _field(self, node: _Node, parents: int) -> FieldSchema:
        total = sum(node.types.values())
        shares = {t: n / total for t, n in node.types.items()} if total else {}
        objects = node.types.get("object", 0)
        schema = FieldSchema(
            bson_types=tuple(t for t, _ in node.types.most_common() if t != "null"),
            required=parents > 0 and node.count == parents,
            nullable="null" in node.types,
            stats=FieldStats(
                count=node.count,
                presence=node.count / parents if parents else 0.0,
                types=shares,
            ),
        )
        if objects:
            if node.overflow:
                schema.open = True
            schema.fields = self._fields(node, parents=objects) if not node.overflow else None
        if node.items is not None and node.items.count:
            schema.items = self._field(node.items, parents=node.items.count)
        return schema


@dataclass(frozen=True)
class InspectResult:
    schema: CollectionSchema
    mode: SampleMode
    documents_scanned: int
    estimated_total: int
    duration_s: float

    @property
    def is_complete(self) -> bool:
        return self.mode == "full" or self.documents_scanned >= self.estimated_total


def inspect_collection(
    db: Database[dict[str, Any]],
    name: str,
    *,
    sample_size: int = 10000,
    sample_percent: float | None = None,
    full_scan: bool = False,
) -> InspectResult:
    """Infer the observed schema of ``name`` and read its indexes and validator."""
    from mongomig.schema.indexes import index_from_server

    coll = db[name]
    start = time.perf_counter()
    estimated = coll.estimated_document_count()

    if full_scan:
        mode: SampleMode = "full"
        limit = None
    elif sample_percent is not None:
        mode = "percent"
        limit = max(1, math.ceil(estimated * sample_percent / 100))
    else:
        mode = "sample"
        limit = sample_size

    acc = SchemaAccumulator()
    acc.add_all(_documents(coll, limit, estimated))
    schema = acc.result(name)
    schema.indexes = [index_from_server(ix) for ix in coll.list_indexes()]

    info = next(db.list_collections(filter={"name": name}), None)
    options = (info or {}).get("options", {})
    if options.get("validator"):
        schema.validator = dict(options["validator"])
        schema.validation_level = options.get("validationLevel", "strict")
        schema.validation_action = options.get("validationAction", "error")

    return InspectResult(
        schema=schema,
        mode=mode if limit is None or limit < estimated else "full",
        documents_scanned=acc.documents,
        estimated_total=estimated,
        duration_s=time.perf_counter() - start,
    )


def _documents(
    coll: Collection[dict[str, Any]], limit: int | None, estimated: int
) -> Iterable[Mapping[str, Any]]:
    if limit is None or limit >= estimated:
        return coll.find({}, batch_size=1000)
    # $sample picks random documents (a pseudo-random cursor for small sample ratios).
    return coll.aggregate([{"$sample": {"size": limit}}], allowDiskUse=True)


def user_collections(db: Database[dict[str, Any]]) -> list[str]:
    """Collections worth inspecting: skips system and MongoMig's own collections."""
    names = db.list_collection_names(filter={"type": "collection"})
    return sorted(n for n in names if not n.startswith(("system.", "__mongomig_")))
