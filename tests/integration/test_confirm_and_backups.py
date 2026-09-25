from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pymongo.database import Database
from typer.testing import CliRunner

from mongomig import upgrade_to_head
from mongomig.cli.app import app
from mongomig.errors import ConfirmationRequiredError
from mongomig.migrations.context import MigrationContext
from tests.helpers import write_migration

pytestmark = pytest.mark.integration
runner = CliRunner()


def applied(db: Database[dict[str, Any]]) -> set[str]:
    return {d["_id"] for d in db["__mongomig_migrations"].find({"status": "applied"})}


@pytest.fixture
def project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mongo_uri: str,
    mongo_db: Database[dict[str, Any]],
) -> Path:
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0
    monkeypatch.setenv("MONGODB_URI", mongo_uri)
    monkeypatch.setenv("MONGODB_DATABASE", mongo_db.name)
    return tmp_path


def test_destructive_upgrade_needs_yes(project: Path, mongo_db: Database[dict[str, Any]]) -> None:
    versions = project / "migrations" / "versions"
    write_migration(versions, "ok1", upgrade='ctx.ops.create_index("u", "a")', minute=0)
    write_migration(versions, "del2", "ok1", upgrade='ctx.ops.unset_field("u", "x")', minute=1)

    result = runner.invoke(app, ["--json", "upgrade"])
    assert result.exit_code == 1
    err = json.loads(result.stdout)["error"]
    assert err["type"] == "ConfirmationRequiredError"
    assert "del2 can delete data" in err["message"]
    assert applied(mongo_db) == set()  # nothing ran, not even the safe one

    with pytest.raises(ConfirmationRequiredError):
        upgrade_to_head(lock_timeout=0)
    assert upgrade_to_head(yes=True).revisions == ["ok1", "del2"]


def test_confirm_modes_from_config(project: Path, mongo_db: Database[dict[str, Any]]) -> None:
    versions = project / "migrations" / "versions"
    write_migration(versions, "a1", minute=0)
    write_migration(versions, "del2", "a1", upgrade='ctx.ops.unset_field("u", "x")', minute=1)
    (project / "mongomig.production.yaml").write_text("execution:\n  confirm: always\n")
    (project / "mongomig.ci.yaml").write_text("execution:\n  confirm: never\n")

    result = runner.invoke(app, ["--env", "production", "upgrade", "a1"])
    assert result.exit_code == 1
    assert "execution.confirm = always" in result.output
    assert runner.invoke(app, ["--env", "production", "upgrade", "a1", "--yes"]).exit_code == 0
    assert runner.invoke(app, ["--env", "ci", "upgrade"]).exit_code == 0
    assert applied(mongo_db) == {"a1", "del2"}


def test_unset_field_backup_and_restore(mongo_db: Database[dict[str, Any]]) -> None:
    users = mongo_db["users"]
    users.insert_many(
        [{"_id": i, "legacy": {"code": i}, "profile": {"bio": f"b{i}"}} for i in range(25)]
        + [{"_id": 100, "other": 1}]
    )
    ctx = MigrationContext(mongo_db, revision="r1", batch_size=10)
    result = ctx.ops.unset_field("users", "legacy", backup=True)
    assert result.modified == 25
    ctx.ops.unset_field("users", "profile.bio", backup=True)
    assert users.count_documents({"legacy": {"$exists": True}}) == 0
    assert mongo_db["__mongomig_backup_r1"].count_documents({}) == 50

    # re-running is safe (idempotent): nothing left to unset, backups untouched
    assert ctx.ops.unset_field("users", "legacy", backup=True).modified == 0
    assert mongo_db["__mongomig_backup_r1"].count_documents({}) == 50

    restored = ctx.ops.restore_field("users", "legacy")
    assert restored.modified == 25
    ctx.ops.restore_field("users", "profile.bio")
    assert users.find_one({"_id": 7}) == {"_id": 7, "profile": {"bio": "b7"}, "legacy": {"code": 7}}
    assert users.find_one({"_id": 100}) == {"_id": 100, "other": 1}
    assert mongo_db["__mongomig_backup_r1"].count_documents({}) == 0


def test_drop_collection_backup_and_restore(mongo_db: Database[dict[str, Any]]) -> None:
    mongo_db["logs"].insert_many([{"i": i} for i in range(5)])
    mongo_db["logs"].create_index("i")
    ctx = MigrationContext(mongo_db, revision="r2")
    ctx.ops.drop_collection("logs", backup=True)
    names = set(mongo_db.list_collection_names())
    assert "logs" not in names
    assert "__mongomig_backup_r2_logs" in names
    ctx.ops.drop_collection("logs", backup=True)  # already moved: no-op

    ctx.ops.restore_collection("logs")
    assert mongo_db["logs"].count_documents({}) == 5
    assert "i_1" in mongo_db["logs"].index_information()  # indexes survive the rename
    ctx.ops.restore_collection("logs")  # nothing to restore: no-op


def test_backups_command(project: Path, mongo_db: Database[dict[str, Any]]) -> None:
    mongo_db["users"].insert_many([{"x": i} for i in range(3)])
    MigrationContext(mongo_db, revision="abc123").ops.unset_field("users", "x", backup=True)
    MigrationContext(mongo_db, revision="def456").ops.drop_collection("users", backup=True)

    listed = json.loads(runner.invoke(app, ["--json", "backups"]).stdout)["backups"]
    assert [b["name"] for b in listed] == [
        "__mongomig_backup_abc123",
        "__mongomig_backup_def456_users",
    ]
    assert listed[0]["documents"] == 3

    refused = runner.invoke(app, ["backups", "--drop", "abc123"])
    assert refused.exit_code == 1
    assert "--yes" in refused.output
    dropped = runner.invoke(app, ["--json", "backups", "--drop", "abc123", "--yes"])
    assert json.loads(dropped.stdout)["dropped"] == ["__mongomig_backup_abc123"]
    assert runner.invoke(app, ["backups", "--drop", "nope", "--yes"]).exit_code == 1
