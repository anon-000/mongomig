from __future__ import annotations

import datetime
import re
import uuid

import pytest
from bson import Binary, Code, Decimal128, Int64, MaxKey, MinKey, ObjectId, Regex, Timestamp

from mongomig.schema import inference
from mongomig.schema.inference import SchemaAccumulator, bson_type_of


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "null"),
        (True, "bool"),
        (5, "int"),
        (2**40, "long"),
        (Int64(5), "long"),
        (1.5, "double"),
        ("s", "string"),
        ({"a": 1}, "object"),
        ([1], "array"),
        (datetime.datetime(2026, 1, 1), "date"),
        (ObjectId(), "objectId"),
        (Decimal128("1.5"), "decimal"),
        (Binary(b"x"), "binData"),
        (b"x", "binData"),
        (uuid.uuid4(), "binData"),
        (Regex("a"), "regex"),
        (re.compile("a"), "regex"),
        (Timestamp(1, 1), "timestamp"),
        (Code("x"), "javascript"),
        (MinKey(), "minKey"),
        (MaxKey(), "maxKey"),
    ],
)
def test_bson_type_of(value: object, expected: str) -> None:
    assert bson_type_of(value) == expected


def infer(docs: list[dict]) -> dict:  # type: ignore[type-arg]
    acc = SchemaAccumulator()
    acc.add_all(docs)
    return acc.result("c").fields  # type: ignore[return-value]


def test_presence_types_and_nullability() -> None:
    docs = [
        {"_id": 1, "name": "a", "age": 30},
        {"_id": 2, "name": "b", "age": "27"},
        {"_id": 3, "name": "c", "age": None},
        {"_id": 4, "name": "d"},
    ]
    fields = infer(docs)
    assert next(iter(fields)) == "_id"
    assert fields["name"].required
    assert fields["name"].stats.presence == 1.0
    age = fields["age"]
    assert not age.required
    assert age.nullable
    assert age.stats.presence == 0.75
    assert age.stats.types == pytest.approx({"int": 1 / 3, "string": 1 / 3, "null": 1 / 3})
    assert set(age.bson_types) == {"int", "string"}


def test_nested_presence_is_relative_to_parent_objects() -> None:
    docs = [
        {"profile": {"verified": True, "bio": "x"}},
        {"profile": {"verified": False}},
        {"profile": None},
        {},
    ]
    profile = infer(docs)["profile"]
    assert profile.stats.presence == 0.75
    assert profile.nullable
    assert profile.fields["verified"].stats.presence == 1.0  # of the 2 profile objects
    assert profile.fields["verified"].required
    assert profile.fields["bio"].stats.presence == 0.5


def test_arrays_track_element_types_and_object_elements() -> None:
    docs = [
        {"tags": ["a", "b"], "items": [{"sku": "x", "qty": 1}, {"sku": "y"}]},
        {"tags": [], "items": [{"sku": "z", "qty": 2}]},
    ]
    fields = infer(docs)
    tags = fields["tags"]
    assert tags.bson_types == ("array",)
    assert tags.items is not None
    assert tags.items.bson_types == ("string",)
    items = fields["items"].items
    assert items is not None
    assert items.fields["sku"].stats.presence == 1.0
    assert items.fields["qty"].stats.presence == pytest.approx(2 / 3)


def test_map_like_objects_overflow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(inference, "MAX_KEYS_PER_OBJECT", 5)
    docs = [{"scores": {f"user{i}": i for i in range(20)}}]
    scores = infer(docs)["scores"]
    assert scores.open
    assert scores.fields is None


def test_empty_collection() -> None:
    assert infer([]) == {}
