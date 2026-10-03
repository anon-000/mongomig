from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from bson import ObjectId

from mongomig.schema.drift import Thresholds, compare, compare_indexes, compare_validator
from mongomig.schema.indexes import build_index, index_from_server
from mongomig.schema.inference import SchemaAccumulator
from mongomig.schema.models import CollectionSchema, FieldSchema

T = Thresholds(missing_field_percent=1, unexpected_type_percent=1, unexpected_field_percent=5)


def f(*types: str, **kw: Any) -> FieldSchema:
    kw.setdefault("required", True)
    return FieldSchema(bson_types=types, **kw)


def declared(**fields: FieldSchema) -> CollectionSchema:
    return CollectionSchema("users", fields={"_id": f("objectId"), **fields})


def observed(docs: list[dict[str, Any]]) -> CollectionSchema:
    acc = SchemaAccumulator()
    acc.add_all({"_id": ObjectId(), **d} for d in docs)
    return acc.result("users")


def findings(model: CollectionSchema, docs: list[dict[str, Any]]) -> dict[str, Any]:
    return {(x.kind, x.path): x for x in compare(model, observed(docs), T)}


def test_clean_data_has_no_drift() -> None:
    docs = [{"name": "a", "age": 30}, {"name": "b", "age": None}]
    assert findings(declared(name=f("string"), age=f("int", nullable=True)), docs) == {}


def test_missing_required_field_and_threshold() -> None:
    docs = [{"status": "a"}] * 99 + [{}]
    one_percent = findings(declared(status=f("string")), docs)[("missing_field", "status")]
    assert one_percent.share == pytest.approx(0.01)
    assert one_percent.status == "warn"  # not above the 1% threshold

    docs = [{"status": "a"}] * 98 + [{}, {}]
    two_percent = findings(declared(status=f("string")), docs)[("missing_field", "status")]
    assert two_percent.status == "fail"
    assert "missing in 2.00% of documents" in two_percent.summary


def test_never_present_field_and_optional_fields() -> None:
    model = declared(new=f("string"), opt=f("string", required=False))
    result = findings(model, [{"x": 1}])
    assert result[("missing_field", "new")].share == 1.0
    assert ("missing_field", "opt") not in result


def test_empty_collection_reports_nothing_about_fields() -> None:
    assert compare(declared(name=f("string")), CollectionSchema("users"), T) == []


def test_unexpected_types() -> None:
    docs = [{"age": 1}] * 90 + [{"age": "27"}] * 8 + [{"age": None}] * 2
    result = findings(declared(age=f("int")), docs)
    finding = result[("unexpected_type", "age")]
    assert finding.status == "fail"
    assert finding.share == pytest.approx(0.10)  # string 8% + null 2% (not nullable)
    assert "string 8.00%" in finding.summary
    assert "null 2.00%" in finding.summary


def test_compatible_types_are_not_drift() -> None:
    docs = [{"n": 1}, {"n": 2**40}, {"price": 3}, {"price": 2.5}]
    model = declared(n=f("int", required=False), price=f("double", required=False))
    assert findings(model, docs) == {}


def test_storage_profile_hint() -> None:
    docs = [{"at": "2026-01-01T00:00:00"}] * 9 + [{"at": datetime(2026, 1, 1, tzinfo=UTC)}]
    finding = findings(declared(at=f("date")), docs)[("unexpected_type", "at")]
    assert "storage='json'" in finding.hint


def test_unexpected_fields() -> None:
    docs = [{"name": "a"}] * 90 + [{"name": "a", "legacy": 1}] * 10
    result = findings(declared(name=f("string")), docs)
    assert result[("unexpected_field", "legacy")].status == "fail"
    assert result[("unexpected_field", "legacy")].share == pytest.approx(0.10)


def test_nested_objects_including_empty_parents() -> None:
    model = declared(profile=f("object", fields={"verified": f("bool")}))
    docs = [{"profile": {"verified": True}}] * 8 + [{"profile": {}}] * 2
    result = findings(model, docs)
    assert result[("missing_field", "profile.verified")].share == pytest.approx(0.2)

    always_empty = findings(model, [{"profile": {}}] * 3)
    assert always_empty[("missing_field", "profile.verified")].share == 1.0


def test_arrays_and_open_objects() -> None:
    model = declared(
        tags=f("array", items=FieldSchema(bson_types=("string",))),
        items=f("array", items=FieldSchema(bson_types=("object",), fields={"sku": f("string")})),
        meta=f("object", open=True),
    )
    docs = [
        {"tags": ["a", 1], "items": [{"sku": "x"}, {"qty": 1}], "meta": {"anything": 1}},
    ]
    result = findings(model, docs)
    assert result[("unexpected_type", "tags[]")].share == pytest.approx(0.5)
    assert result[("missing_field", "items[].sku")].share == pytest.approx(0.5)
    assert ("unexpected_field", "items[].qty") in result
    assert not any(path and path.startswith("meta.") for _, path in result)  # open: not tracked


def test_any_typed_fields_are_not_checked() -> None:
    assert findings(declared(x=f()), [{"x": 1}, {"x": "s"}, {"x": [1]}]) == {}


def test_index_comparison() -> None:
    declared_ix = [
        build_index("email", unique=True, name="users_email_unique"),
        build_index("name"),
        build_index([("title", "text")], name="title_text"),
        build_index("city", collation={"locale": "en", "strength": 2}),
        build_index("gone"),
    ]
    server = [
        index_from_server({"v": 2, "key": {"_id": 1}, "name": "_id_"}),
        index_from_server(
            {"v": 2, "key": {"email": 1}, "name": "users_email_unique"}
        ),  # not unique
        index_from_server({"v": 2, "key": {"name": 1}, "name": "name_1"}),
        index_from_server(
            {
                "v": 2,
                "key": {"_fts": "text", "_ftsx": 1},
                "name": "title_text",
                "weights": {"title": 1},
                "default_language": "english",
                "textIndexVersion": 3,
            }
        ),
        index_from_server(
            {
                "v": 2,
                "key": {"city": 1},
                "name": "city_1",
                "collation": {
                    "locale": "en",
                    "strength": 2,
                    "caseLevel": False,
                    "alternate": "non-ignorable",
                },
            }
        ),
        index_from_server({"v": 2, "key": {"legacy": 1}, "name": "legacy_1"}),
    ]
    result = {
        (x.kind, x.summary.split(" ")[1]): x.status
        for x in compare_indexes("users", declared_ix, server)
    }
    assert result == {
        ("index_mismatch", "users_email_unique:"): "fail",
        ("index_missing", "gone_1"): "fail",
        ("index_unexpected", "legacy_1"): "warn",
    }  # text index and collation defaults match


def test_validator_comparison() -> None:
    def coll(v: dict[str, Any] | None, level: str | None = None) -> CollectionSchema:
        return CollectionSchema(
            "users", validator=v, validation_level=level, validation_action="error" if v else None
        )

    rules = {"$jsonSchema": {"required": ["email"]}}
    assert compare_validator(coll(None), coll(rules, "strict")) == []  # not managed
    assert compare_validator(coll(rules, "moderate"), coll(rules, "moderate")) == []
    assert compare_validator(coll(rules, "moderate"), coll(None))[0].kind == "validator_missing"
    mismatch = compare_validator(coll(rules, "moderate"), coll(rules, "strict"))[0]
    assert mismatch.kind == "validator_mismatch"
    assert "level strict" in mismatch.summary
    other = compare_validator(
        coll(rules, "moderate"), coll({"$jsonSchema": {"required": []}}, "moderate")
    )[0]
    assert "rules differ" in other.summary


def test_fields_defaulting_to_none_are_not_missing() -> None:
    model = declared(
        plan=f("string", nullable=True, has_default=True, default=None, default_is_static=True),
        status=f("string", has_default=True, default="active", default_is_static=True),
    )
    result = findings(model, [{"x": 1}])
    assert ("missing_field", "plan") not in result  # reads as None: autogenerate skips it too
    assert ("missing_field", "status") in result  # queries on status would miss these
