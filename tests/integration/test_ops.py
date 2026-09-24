from __future__ import annotations

from typing import Any

import pytest
from pymongo.database import Database
from pymongo.errors import OperationFailure

from mongomig.migrations.context import MigrationContext
from mongomig.migrations.reporting import Reporter

pytestmark = pytest.mark.integration


class Recorder(Reporter):
    def __init__(self) -> None:
        self.logs: list[str] = []
        self.progress_calls: list[tuple[str, int, int | None]] = []

    def log(self, message: str) -> None:
        self.logs.append(message)

    def progress(self, task: str, done: int, total: int | None) -> None:
        self.progress_calls.append((task, done, total))


@pytest.fixture
def ctx(mongo_db: Database[dict[str, Any]]) -> MigrationContext:
    return MigrationContext(mongo_db, batch_size=100, reporter=Recorder())


def options(db: Database[dict[str, Any]], name: str) -> dict[str, Any]:
    info = next(db.list_collections(filter={"name": name}))
    return info["options"]  # type: ignore[no-any-return]


def test_create_index_idempotent(ctx: MigrationContext, mongo_db: Database[dict[str, Any]]) -> None:
    assert ctx.ops.create_index("users", "email", unique=True, name="users_email_unique") == (
        "users_email_unique"
    )
    ctx.ops.create_index("users", "email", unique=True, name="users_email_unique")  # again: ok
    info = mongo_db["users"].index_information()["users_email_unique"]
    assert info["unique"] is True
    assert info["key"] == [("email", 1)]

    with pytest.raises(OperationFailure):  # same name, different definition
        ctx.ops.create_index("users", "name", name="users_email_unique")


def test_index_options(ctx: MigrationContext, mongo_db: Database[dict[str, Any]]) -> None:
    ctx.ops.create_index("sessions", "created_at", expireAfterSeconds=3600, name="ttl")
    ctx.ops.create_index(
        "users",
        [("status", 1), ("created_at", -1)],
        partialFilterExpression={"status": "active"},
    )
    assert mongo_db["sessions"].index_information()["ttl"]["expireAfterSeconds"] == 3600
    assert "status_1_created_at_-1" in mongo_db["users"].index_information()


def test_drop_index_missing_is_ok(
    ctx: MigrationContext, mongo_db: Database[dict[str, Any]]
) -> None:
    ctx.ops.drop_index("nope", "nope_1")  # collection missing
    mongo_db["users"].insert_one({})
    ctx.ops.drop_index("users", "nope_1")  # index missing
    ctx.ops.create_index("users", "x")
    ctx.ops.drop_index("users", "x_1")
    assert "x_1" not in mongo_db["users"].index_information()


def test_collections_and_validators(
    ctx: MigrationContext, mongo_db: Database[dict[str, Any]]
) -> None:
    schema = {"$jsonSchema": {"bsonType": "object", "required": ["email"]}}
    ctx.ops.create_collection("users", validator=schema)
    ctx.ops.create_collection("users", validator=schema)  # idempotent
    opts = options(mongo_db, "users")
    assert opts["validator"] == schema
    assert opts["validationLevel"] == "moderate"

    stricter = {"$jsonSchema": {"bsonType": "object", "required": ["email", "name"]}}
    ctx.ops.set_validator("users", stricter, level="strict")
    assert options(mongo_db, "users")["validator"] == stricter
    assert options(mongo_db, "users")["validationLevel"] == "strict"

    ctx.ops.set_validator("orders", schema)  # creates missing collection
    assert options(mongo_db, "orders")["validator"] == schema

    ctx.ops.remove_validator("users")
    assert options(mongo_db, "users").get("validator", {}) == {}

    ctx.ops.rename_collection("orders", "purchases")
    assert set(mongo_db.list_collection_names()) >= {"users", "purchases"}
    ctx.ops.drop_collection("purchases")
    assert "purchases" not in mongo_db.list_collection_names()


def test_backfill_batches_and_is_rerunnable(
    ctx: MigrationContext, mongo_db: Database[dict[str, Any]]
) -> None:
    users = mongo_db["users"]
    users.insert_many([{"n": i} for i in range(250)] + [{"n": -1, "status": "banned"}])

    result = ctx.ops.backfill(
        "users", {"status": {"$exists": False}}, {"$set": {"status": "active"}}
    )
    assert (result.matched, result.modified, result.batches) == (250, 250, 3)
    assert users.count_documents({"status": "active"}) == 250
    assert users.count_documents({"status": "banned"}) == 1  # untouched

    reporter = ctx.reporter
    assert isinstance(reporter, Recorder)
    assert reporter.progress_calls[0] == ("backfill users", 0, 250)
    assert reporter.progress_calls[-1] == ("backfill users", 250, 250)

    again = ctx.ops.backfill("users", {"status": {"$exists": False}}, {"$set": {"status": "x"}})
    assert (again.matched, again.modified) == (0, 0)


def test_backfill_pipeline_and_mixed_id_types(
    ctx: MigrationContext, mongo_db: Database[dict[str, Any]]
) -> None:
    users = mongo_db["users"]
    users.insert_many(
        [{"_id": i, "first": "A", "last": str(i)} for i in range(150)]
        + [{"_id": f"s{i}", "first": "B", "last": str(i)} for i in range(60)]
    )
    ctx.ops.backfill(
        "users",
        {"full": {"$exists": False}},
        [{"$set": {"full": {"$concat": ["$first", " ", "$last"]}}}],
    )
    assert users.count_documents({"full": {"$exists": True}}) == 210
    assert users.find_one({"_id": "s3"})["full"] == "B 3"  # type: ignore[index]


def test_unset_and_rename_field(ctx: MigrationContext, mongo_db: Database[dict[str, Any]]) -> None:
    users = mongo_db["users"]
    users.insert_many([{"legacy": 1, "fname": "a"}, {"fname": "b"}, {"other": True}])

    assert ctx.ops.unset_field("users", "legacy").modified == 1
    assert users.count_documents({"legacy": {"$exists": True}}) == 0

    assert ctx.ops.rename_field("users", "fname", "first_name").modified == 2
    assert users.count_documents({"first_name": {"$exists": True}}) == 2
    assert users.count_documents({"fname": {"$exists": True}}) == 0


def test_collection_and_unsafe_db_are_plain_pymongo(
    ctx: MigrationContext, mongo_db: Database[dict[str, Any]]
) -> None:
    ctx.collection("things").insert_one({"a": 1})
    assert ctx.unsafe_db["things"].count_documents({}) == 1
    assert ctx.database_name == mongo_db.name
