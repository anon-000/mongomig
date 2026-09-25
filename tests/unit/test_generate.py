from __future__ import annotations

import ast
from typing import Any

from mongomig.generators.render import generate, lit
from mongomig.schema.diff import diff_schemas, parse_renames
from mongomig.schema.indexes import build_index
from mongomig.schema.models import CollectionSchema, FieldSchema


def f(*types: str, **kw: Any) -> FieldSchema:
    kw.setdefault("required", True)
    return FieldSchema(bson_types=types, **kw)


def coll(name: str = "users", **fields: FieldSchema) -> CollectionSchema:
    return CollectionSchema(name=name, fields={"_id": f("objectId"), **fields})


def code(old: dict, new: dict, renames: list[str] | None = None) -> tuple[str, str, Any]:  # type: ignore[type-arg]
    diff = diff_schemas(old, new, renames=parse_renames(renames or []))
    gen = generate(diff)
    for body in (gen.upgrade_body, gen.downgrade_body):
        ast.parse(f"def f(ctx):\n{body}\n")  # always valid Python
    return gen.upgrade_body, gen.downgrade_body, gen


def calls(body: str) -> list[tuple[str, list[Any], dict[str, Any]]]:
    """The live (uncommented) ctx.ops calls in a generated body, parsed back to values."""
    fn = ast.parse(f"def f(ctx):\n{body}\n").body[0]
    assert isinstance(fn, ast.FunctionDef)
    result = []
    for stmt in fn.body:
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            call = stmt.value
            result.append(
                (
                    ast.unparse(call.func),
                    [ast.literal_eval(a) for a in call.args],
                    {k.arg: ast.literal_eval(k.value) for k in call.keywords if k.arg},
                )
            )
    return result


def names(body: str) -> list[str]:
    return [c[0] for c in calls(body)]


def test_backfill_with_default_and_reverse() -> None:
    up, down, gen = code(
        {"users": coll()},
        {
            "users": coll(
                status=f("string", has_default=True, default="active", default_is_static=True)
            )
        },
    )
    assert calls(up) == [
        (
            "ctx.ops.backfill",
            ["users", {"status": {"$exists": False}}, {"$set": {"status": "active"}}],
            {},
        )
    ]
    assert calls(down) == [("ctx.ops.unset_field", ["users", "status"], {})]
    assert gen.review == []


def test_nested_backfill_filters_on_parent_object() -> None:
    old = coll(profile=f("object", fields={}))
    new = coll(
        profile=f(
            "object",
            fields={"verified": f("bool", has_default=True, default=False, default_is_static=True)},
        )
    )
    up, _, _ = code({"users": old}, {"users": new})
    assert calls(up) == [
        (
            "ctx.ops.backfill",
            [
                "users",
                {"profile": {"$type": "object"}, "profile.verified": {"$exists": False}},
                {"$set": {"profile.verified": False}},
            ],
            {},
        )
    ]


def test_manual_review_items_are_commented_out() -> None:
    up, _, gen = code(
        {"users": coll(age=f("string"), old=f("string"))},
        {"users": coll(age=f("int"), email=f("string"))},
    )
    assert calls(up) == []  # nothing risky runs without a human
    assert up.rstrip().endswith("pass")
    assert "TODO(review): email" in up
    assert "TODO(review): age" in up
    assert '"$convert"' in up
    assert '# ctx.ops.unset_field("users", "old")' in up
    assert len(gen.review) == 2


def test_rename_both_directions() -> None:
    up, down, _ = code(
        {"users": coll(first_name=f("string"))},
        {"users": coll(given_name=f("string"))},
        ["users.first_name:given_name"],
    )
    assert calls(up) == [("ctx.ops.rename_field", ["users", "first_name", "given_name"], {})]
    assert calls(down) == [("ctx.ops.rename_field", ["users", "given_name", "first_name"], {})]


def test_phases_and_downgrade_order() -> None:
    old = coll()
    old.indexes = [build_index("legacy")]
    new = coll(status=f("string", has_default=True, default="a", default_is_static=True))
    new.indexes = [build_index("status")]
    new.validator = {"$jsonSchema": {"required": ["status"]}}
    new.validation_level, new.validation_action = "moderate", "error"
    orders = coll("orders")
    up, down, _ = code({"users": old}, {"users": new, "orders": orders})

    assert names(up) == [
        "ctx.ops.create_collection",
        "ctx.ops.backfill",
        "ctx.ops.drop_index",
        "ctx.ops.create_index",
        "ctx.ops.set_validator",
    ]
    assert names(down) == [
        "ctx.ops.remove_validator",
        "ctx.ops.drop_index",
        "ctx.ops.create_index",
        "ctx.ops.unset_field",
    ]
    assert '# ctx.ops.drop_collection("orders")' in down  # never live


def test_index_rendering() -> None:
    new = coll()
    new.indexes = [
        build_index("email", unique=True, name="users_email_unique"),
        build_index([("a", 1), ("b", -1)], partialFilterExpression={"a": {"$gt": 1}}),
    ]
    up, down, _ = code({"users": coll()}, {"users": new})
    assert 'ctx.ops.create_index("users", "email", name="users_email_unique", unique=True)' in up
    assert '[("a", 1), ("b", -1)]' in up
    assert 'partialFilterExpression={"a": {"$gt": 1}}' in up
    assert 'ctx.ops.drop_index("users", "users_email_unique")' in down


def test_no_changes_gives_pass() -> None:
    up, down, _ = code({"users": coll()}, {"users": coll()})
    assert up.strip() == "pass"
    assert down.strip() == "pass"


def test_lit() -> None:
    assert lit({"a": [1, None, True], "b": 'q"uote'}) == '{"a": [1, None, True], "b": "q\\"uote"}'
    assert lit(("x",)) == '("x",)'
    long_value = {f"key{i}": "v" * 10 for i in range(10)}
    rendered = lit(long_value)
    assert rendered.startswith("{\n")
    assert ast.literal_eval(rendered) == long_value
