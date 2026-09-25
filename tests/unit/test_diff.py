from __future__ import annotations

from typing import Any

import pytest

from mongomig.errors import ValidationError
from mongomig.schema.diff import Severity, diff_schemas, parse_renames
from mongomig.schema.indexes import build_index
from mongomig.schema.models import CollectionSchema, FieldSchema


def f(*types: str, **kw: Any) -> FieldSchema:
    kw.setdefault("required", True)
    return FieldSchema(bson_types=types, **kw)


def coll(name: str = "users", **fields: FieldSchema) -> CollectionSchema:
    return CollectionSchema(name=name, fields={"_id": f("objectId"), **fields})


def changes(old: CollectionSchema | None, new: CollectionSchema | None, **kw: Any) -> list:  # type: ignore[type-arg]
    before = {old.name: old} if old else {}
    after = {new.name: new} if new else {}
    return diff_schemas(before, after, **kw).changes


def one(old: CollectionSchema, new: CollectionSchema) -> Any:
    result = changes(old, new)
    assert len(result) == 1, [c.summary for c in result]
    return result[0]


def test_identical_schemas_have_no_changes() -> None:
    a = coll(title=f("string"))
    assert changes(a, coll(title=f("string"))) == []


@pytest.mark.parametrize(
    ("field", "severity"),
    [
        (f("string", required=False), Severity.SAFE),
        (
            f("string", has_default=True, default="x", default_is_static=True),
            Severity.REQUIRES_DATA_MIGRATION,
        ),
        (f("int", nullable=True), Severity.REQUIRES_DATA_MIGRATION),  # required nullable → null
        (f("string"), Severity.MANUAL_REVIEW),  # required, no default
        (f("date", has_default=True), Severity.MANUAL_REVIEW),  # dynamic default
    ],
)
def test_field_added(field: FieldSchema, severity: Severity) -> None:
    change = one(coll(), coll(new=field))
    assert change.kind == "field_added"
    assert change.path == "new"
    assert change.severity == severity


def test_field_removed_keeps_data() -> None:
    change = one(coll(old=f("string")), coll())
    assert change.kind == "field_removed"
    assert change.severity == Severity.WARNING
    assert "kept" in change.note


@pytest.mark.parametrize(
    ("old", "new", "severity"),
    [
        (f("int"), f("int", "string"), Severity.SAFE),  # widened
        (f("int"), f("long"), None),  # same integer family: no change at all
        (f("int"), f("double"), Severity.WARNING),  # safe conversion
        (f("string"), f("int"), Severity.MANUAL_REVIEW),  # unsafe
        (f("int", "string"), f("int"), Severity.MANUAL_REVIEW),  # narrowed
        (f("string"), f(), Severity.SAFE),  # became Any
    ],
)
def test_type_changes(old: FieldSchema, new: FieldSchema, severity: Severity | None) -> None:
    result = changes(coll(x=old), coll(x=new))
    if severity is None:
        assert result == []
    else:
        assert [c.kind for c in result] == ["field_type_changed"]
        assert result[0].severity == severity


def test_nullable_and_required_transitions() -> None:
    to_not_null = one(
        coll(x=f("string", nullable=True)),
        coll(x=f("string", has_default=True, default="d", default_is_static=True)),
    )
    assert to_not_null.kind == "field_nullable_changed"
    assert to_not_null.severity == Severity.REQUIRES_DATA_MIGRATION

    no_default = one(coll(x=f("string", nullable=True)), coll(x=f("string")))
    assert no_default.severity == Severity.MANUAL_REVIEW

    assert one(coll(x=f("string")), coll(x=f("string", nullable=True))).severity == Severity.SAFE

    now_required = one(
        coll(x=f("string", required=False)),
        coll(x=f("string", has_default=True, default="d", default_is_static=True)),
    )
    assert now_required.kind == "field_required_changed"
    assert now_required.severity == Severity.REQUIRES_DATA_MIGRATION
    assert one(coll(x=f("string")), coll(x=f("string", required=False))).severity == Severity.SAFE


def test_enum_changes() -> None:
    base = coll(s=f("string", enum=("a", "b")))
    assert one(base, coll(s=f("string", enum=("a", "b", "c")))).severity == Severity.SAFE
    removed = one(base, coll(s=f("string", enum=("a",))))
    assert removed.severity == Severity.MANUAL_REVIEW
    assert "['b']" in removed.summary
    assert one(coll(s=f("string")), base).severity == Severity.WARNING


def test_nested_objects_and_arrays() -> None:
    old = coll(
        profile=f("object", fields={"bio": f("string", required=False)}),
        items=f("array", items=FieldSchema(bson_types=("object",), fields={"sku": f("string")})),
    )
    new = coll(
        profile=f(
            "object",
            fields={
                "bio": f("string", required=False),
                "verified": f("bool", has_default=True, default=False, default_is_static=True),
            },
        ),
        items=f(
            "array",
            items=FieldSchema(
                bson_types=("object",),
                fields={
                    "sku": f("string"),
                    "qty": f("int", has_default=True, default=1, default_is_static=True),
                },
            ),
        ),
    )
    result = {c.path: c for c in changes(old, new)}
    assert result["profile.verified"].severity == Severity.REQUIRES_DATA_MIGRATION
    assert result["items[].qty"].severity == Severity.MANUAL_REVIEW  # can't backfill in arrays
    assert result["items[].qty"].in_array


def test_explicit_rename_and_hints() -> None:
    old = coll(first_name=f("string"))
    new = coll(given_name=f("string"))

    hinted = diff_schemas({"users": old}, {"users": new})
    assert [c.kind for c in hinted.changes] == ["field_added", "field_removed"]
    hint = hinted.rename_hints[0]
    assert (hint.old_path, hint.new_path) == ("first_name", "given_name")
    assert hint.flag == "--rename users.first_name:given_name"

    renamed = diff_schemas(
        {"users": old}, {"users": new}, renames=parse_renames(["users.first_name:given_name"])
    )
    assert [c.kind for c in renamed.changes] == ["field_renamed"]
    assert renamed.changes[0].target == "given_name"
    assert renamed.rename_hints == []

    unused = diff_schemas({"users": old}, {"users": new}, renames={("users", "nope"): "x"})
    assert any("did not match" in w for w in unused.warnings)


def test_rename_with_type_change_reports_both() -> None:
    result = diff_schemas(
        {"users": coll(age_str=f("string"))},
        {"users": coll(age=f("int"))},
        renames={("users", "age_str"): "age"},
    ).changes
    assert [c.kind for c in result] == ["field_renamed", "field_type_changed"]
    assert result[1].path == "age"


@pytest.mark.parametrize("spec", ["nodot:x", "users.a", "users.a:b.c", "users.:b"])
def test_parse_renames_rejects_bad_specs(spec: str) -> None:
    with pytest.raises(ValidationError):
        parse_renames([spec])


def test_collections_added_and_removed() -> None:
    new = coll("orders", total=f("double"))
    new.indexes = [build_index("total")]
    added = changes(None, new)
    assert [c.kind for c in added] == ["collection_added", "index_added"]
    removed = changes(coll("orders"), None)
    assert [(c.kind, c.severity) for c in removed] == [("collection_removed", Severity.WARNING)]


def test_index_changes_and_risk() -> None:
    old = coll()
    old.indexes = [build_index("_id", name="_id_"), build_index("a"), build_index("b")]
    new = coll()
    new.indexes = [
        build_index("a", unique=True),  # same name, now unique → changed
        build_index("c", expireAfterSeconds=60),  # TTL
        build_index("d", unique=True, name="d_unique"),
    ]
    result = {(c.kind, (c.new or c.old).name): c for c in changes(old, new)}
    assert set(result) == {
        ("index_changed", "a_1"),
        ("index_added", "c_1"),
        ("index_added", "d_unique"),
        ("index_removed", "b_1"),
    }
    assert "TTL" in result[("index_added", "c_1")].note
    assert "duplicates" in result[("index_added", "d_unique")].note


def test_validator_changes() -> None:
    def with_validator(v: dict | None, level: str = "moderate") -> CollectionSchema:  # type: ignore[type-arg]
        c = coll()
        c.validator, c.validation_level, c.validation_action = v, level, "error"
        return c

    v1, v2 = {"$jsonSchema": {"required": ["a"]}}, {"$jsonSchema": {"required": ["b"]}}
    assert changes(with_validator(None), with_validator(None)) == []
    assert one(with_validator(None), with_validator(v1)).kind == "validator_added"
    assert one(with_validator(v1), with_validator(v2)).kind == "validator_changed"
    assert one(with_validator(v1), with_validator(v1, "strict")).kind == "validator_changed"
    assert changes(with_validator(v1), with_validator(v1)) == []
    assert one(with_validator(v1), with_validator(None)).kind == "validator_removed"


def test_result_summaries() -> None:
    result = diff_schemas({"users": coll()}, {"users": coll(x=f("string", required=False))})
    assert result.max_severity == Severity.SAFE
    assert result.counts() == {"SAFE": 1}
    data = result.to_dict()
    assert data["changes"][0]["new"]["bson_types"] == ["string"]


def test_field_added_with_none_default_needs_no_backfill() -> None:
    change = one(
        coll(),
        coll(age=f("int", nullable=True, has_default=True, default=None, default_is_static=True)),
    )
    assert change.severity == Severity.SAFE
    assert "no backfill" in change.note


def test_none_default_needs_backfill_under_strict_validator() -> None:
    field = f("int", nullable=True, has_default=True, default=None, default_is_static=True)
    strict = coll(age=field)
    strict.validator, strict.validation_level = {"$jsonSchema": {}}, "strict"
    old = coll()
    old.validator, old.validation_level = {"$jsonSchema": {}}, "strict"
    change = next(c for c in changes(old, strict) if c.kind == "field_added")
    assert change.severity == Severity.REQUIRES_DATA_MIGRATION  # strict rejects docs without it
