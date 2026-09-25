from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pymongo.database import Database
from typer.testing import CliRunner

from mongomig.cli.app import app
from mongomig.migrations.context import MigrationContext
from mongomig.migrations.dryrun import DryRunUnavailable, Recorder
from tests.helpers import write_migration

pytestmark = pytest.mark.integration
runner = CliRunner()


def snapshot_db(db: Database[dict[str, Any]]) -> dict[str, Any]:
    """Everything a dry run must not change."""
    state: dict[str, Any] = {}
    for name in sorted(db.list_collection_names()):
        info = next(db.list_collections(filter={"name": name}))
        state[name] = {
            "docs": list(db[name].find({}, sort=[("_id", 1)])),
            "indexes": sorted(db[name].index_information()),
            "options": info.get("options", {}),
        }
    return state


@pytest.fixture
def seeded(mongo_db: Database[dict[str, Any]]) -> Database[dict[str, Any]]:
    mongo_db["users"].insert_many(
        [{"email": f"u{i % 90}@x", "legacy": i % 2, "n": i} for i in range(100)]
    )
    mongo_db["users"].create_index("n", name="n_1")
    mongo_db["logs"].insert_many([{"i": i} for i in range(10)])
    return mongo_db


def dry_ctx(db: Database[dict[str, Any]]) -> tuple[MigrationContext, Recorder]:
    rec = Recorder()
    return MigrationContext(db, revision="rev1", recorder=rec), rec


def test_ops_are_recorded_not_executed(seeded: Database[dict[str, Any]]) -> None:
    before = snapshot_db(seeded)
    ctx, rec = dry_ctx(seeded)
    assert ctx.dry_run

    ctx.ops.backfill("users", {"status": {"$exists": False}}, {"$set": {"status": "a"}})
    ctx.ops.backfill("users", {"n": {"$lt": 10}}, {"$set": {"low": True}})
    ctx.ops.unset_field("users", "legacy", filter={"legacy": 1})
    ctx.ops.unset_field("users", "legacy", backup=True)
    ctx.ops.rename_field("users", "email", "mail")
    assert ctx.ops.create_index("users", "email", unique=True) == "email_1"
    ctx.ops.create_index("users", "n", name="n_1")
    ctx.ops.drop_index("users", "n_1")
    ctx.ops.set_validator("users", {"$jsonSchema": {"required": ["status"]}}, level="strict")
    ctx.ops.create_collection("new_coll")
    ctx.ops.drop_collection("logs")
    ctx.ops.drop_collection("logs", backup=True)
    ctx.ops.rename_collection("users", "people")

    assert snapshot_db(seeded) == before  # nothing was written

    ops = {(o.operation, o.detail.split(" ")[0].rstrip(",")): o for o in rec.ops}
    missing_status = rec.ops[0]
    assert missing_status.estimated_docs == 100
    assert missing_status.collection_scan is True
    indexed = rec.ops[1]
    assert indexed.estimated_docs == 10
    assert indexed.collection_scan is False  # served by the n_1 index
    unset_filtered, unset_backup = rec.ops[2], rec.ops[3]
    assert unset_filtered.estimated_docs == 50
    assert unset_filtered.destructive
    assert not unset_backup.destructive
    assert "backup → __mongomig_backup_rev1" in unset_backup.detail

    unique = ops[("create_index", "email_1")]
    assert unique.estimated_docs == 100
    assert unique.warnings
    assert "will fail: duplicate values" in unique.warnings[0]
    assert "already exists" in ops[("create_index", "n_1")].detail

    validator = ops[("set_validator", "level=strict")]
    assert "~100 existing documents don't match" in validator.warnings[0]
    drops = [o for o in rec.ops if o.operation == "drop_collection"]
    assert [d.destructive for d in drops] == [True, False]
    assert drops[0].estimated_docs == 10


def test_raw_collection_writes_are_recorded(seeded: Database[dict[str, Any]]) -> None:
    before = snapshot_db(seeded)
    ctx, rec = dry_ctx(seeded)
    users = ctx.collection("users")

    # reads pass through
    assert users.count_documents({}) == 100
    assert users.find_one({"n": 3})["n"] == 3  # type: ignore[index]

    for doc in users.find({"n": {"$lt": 5}}):
        result = users.update_one({"_id": doc["_id"]}, {"$set": {"vip": True}})
        assert result.acknowledged is False  # tells code nothing was written
    users.insert_one({"x": 1})
    users.delete_many({"legacy": 0})
    ctx.collection("logs").drop()
    users.aggregate([{"$match": {}}, {"$out": "copy"}])

    assert snapshot_db(seeded) == before
    summary = [(o.operation, o.calls, o.destructive, o.exact) for o in rec.ops]
    assert summary == [
        ("update_one", 5, False, False),
        ("insert_one", 1, False, False),
        ("delete_many", 1, True, False),
        ("drop", 1, True, False),
        ("aggregate", 1, False, False),
    ]
    assert rec.ops[2].estimated_docs == 50

    with pytest.raises(DryRunUnavailable):
        _ = ctx.unsafe_db
    with pytest.raises(DryRunUnavailable):
        users.some_unknown_method()


@pytest.fixture
def project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mongo_uri: str,
    seeded: Database[dict[str, Any]],
) -> Path:
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0
    monkeypatch.setenv("MONGODB_URI", mongo_uri)
    monkeypatch.setenv("MONGODB_DATABASE", seeded.name)
    return tmp_path / "migrations" / "versions"


def test_plan_and_dry_run_commands(project: Path, seeded: Database[dict[str, Any]]) -> None:
    write_migration(
        project,
        "a1",
        upgrade='ctx.ops.backfill("users", {"s": {"$exists": False}}, {"$set": {"s": 1}})',
        downgrade='ctx.ops.unset_field("users", "s")',
        minute=0,
    )
    write_migration(project, "b2", "a1", upgrade="_ = ctx.unsafe_db", minute=1)
    write_migration(project, "c3", "b2", upgrade='raise KeyError("boom")', minute=2)
    before = snapshot_db(seeded)

    result = runner.invoke(app, ["plan"])
    assert result.exit_code == 0, result.output
    assert "a1  rev a1" in result.output
    assert "pending" in result.output
    assert "~100 docs · collection scan" in result.output
    assert "not fully simulated: the migration uses ctx.unsafe_db" in result.output
    assert "dry run raised: KeyError" in result.output

    data = json.loads(runner.invoke(app, ["--json", "upgrade", "--dry-run"]).stdout)
    by_rev = {m["revision"]: m for m in data["migrations"]}
    assert by_rev["a1"]["risk"] == "LOW"
    assert by_rev["a1"]["resumable"] is True
    assert by_rev["b2"]["risk"] == "MEDIUM"
    assert by_rev["c3"]["risk"] == "HIGH"
    assert data["risk"] == "HIGH"
    assert snapshot_db(seeded) == before  # plan and --dry-run changed nothing

    # apply a1 only, then preview its downgrade
    assert runner.invoke(app, ["upgrade", "a1"]).exit_code == 0
    down = json.loads(runner.invoke(app, ["--json", "downgrade", "--dry-run"]).stdout)
    assert down["migrations"][0]["revision"] == "a1"
    assert down["migrations"][0]["direction"] == "downgrade"
    assert down["migrations"][0]["operations"][0]["destructive"] is True
    assert seeded["users"].count_documents({"s": 1}) == 100  # dry-run downgrade kept data
