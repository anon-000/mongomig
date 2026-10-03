"""Squash end to end: old, partially migrated and new databases all end in the same state."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from pymongo import MongoClient
from pymongo.database import Database
from typer.testing import CliRunner

from mongomig.cli.app import app
from tests.helpers import write_migration

pytestmark = pytest.mark.integration
runner = CliRunner()

HISTORY = [
    (
        'ctx.ops.create_collection("users")\n'
        'ctx.ops.create_index("users", "email", unique=True, name="users_email_unique")\n'
        'ctx.ops.create_index("users", "legacy")'
    ),
    (
        'ctx.ops.backfill("users", {"status": {"$exists": False}}, {"$set": {"status": "a"}})\n'
        'ctx.ops.drop_index("users", "legacy_1")'
    ),
    (
        'ctx.ops.create_index("orders", [("user_id", 1), ("at", -1)])\n'
        'ctx.ops.set_validator("users", {"$jsonSchema": {"required": ["email"]}})'
    ),
    (
        'ctx.ops.create_collection("logs")\n'
        'ctx.ops.create_index("logs", "at")\n'
        'ctx.ops.rename_collection("logs", "events")'
    ),
]


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mongo_uri: str) -> Path:
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0
    monkeypatch.setenv("MONGODB_URI", mongo_uri)
    versions = tmp_path / "migrations" / "versions"
    for i, body in enumerate(HISTORY):
        write_migration(versions, f"r{i + 1}", f"r{i}" if i else None, upgrade=body, minute=i)
    write_migration(
        versions, "r5", "r4", upgrade='ctx.ops.create_index("users", "status")', minute=10
    )
    return tmp_path


@pytest.fixture
def dbs(mongo_client: MongoClient[dict[str, Any]]) -> Any:
    names: list[str] = []

    def make(suffix: str) -> Database[dict[str, Any]]:
        name = f"mongomig_sq_{suffix}"
        mongo_client.drop_database(name)
        names.append(name)
        return mongo_client[name]

    yield make
    for name in names:
        mongo_client.drop_database(name)


def cli(db: Database[dict[str, Any]], *args: str) -> Any:
    result = runner.invoke(app, ["--json", *args], env={"MONGODB_DATABASE": db.name})
    return result.exit_code, (json.loads(result.stdout) if result.stdout.strip() else {})


def schema(db: Database[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for name in sorted(db.list_collection_names()):
        if name.startswith("__mongomig"):
            continue
        info = next(db.list_collections(filter={"name": name}))
        out[name] = (sorted(db[name].index_information()), info["options"].get("validator"))
    return out


def squash(project: Path, to: str = "r4") -> str:
    result = runner.invoke(app, ["--json", "squash", to, "-m", "squash"])
    assert result.exit_code == 0, result.output
    return str(json.loads(result.stdout)["revision"])


def test_old_partial_and_new_databases_converge(project: Path, dbs: Any) -> None:
    old, partial, new = dbs("old"), dbs("partial"), dbs("new")
    assert cli(old, "upgrade", "r4")[0] == 0  # ran the whole history before the squash
    assert cli(partial, "upgrade", "r2")[0] == 0  # ran only half of it
    reference = dbs("reference")
    assert cli(reference, "upgrade")[0] == 0  # unsquashed history, all the way

    s = squash(project)

    code, data = cli(old, "upgrade")
    assert code == 0
    assert data["adopted"] == [s]
    assert [step["revision"] for step in data["applied"]] == ["r5"]

    code, data = cli(partial, "upgrade")
    assert code == 0
    assert [step["revision"] for step in data["applied"]] == ["r3", "r4", "r5"]  # from archive
    assert data["adopted"] == [s]

    code, data = cli(new, "upgrade")
    assert code == 0
    assert [step["revision"] for step in data["applied"]] == [s, "r5"]

    expected = schema(reference)
    assert schema(old) == expected
    assert schema(partial) == expected
    assert schema(new) == expected
    for db in (old, partial, new):
        current = cli(db, "current")[1]
        assert current["up_to_date"] is True
        assert current["unknown"] == []


def test_partial_database_without_archive(project: Path, dbs: Any) -> None:
    db = dbs("noarchive")
    cli(db, "upgrade", "r2")
    s = squash(project)
    shutil.rmtree(project / "migrations" / "versions" / "_squashed")
    code, data = cli(db, "upgrade")
    assert code == 4
    assert "applied only part" in data["error"]["message"]
    assert s in data["error"]["message"]


def test_downgrade_through_an_adopted_squash(project: Path, dbs: Any) -> None:
    db = dbs("down")
    cli(db, "upgrade", "r4")
    s = squash(project)
    cli(db, "upgrade")
    code, _ = cli(db, "downgrade", "base", "--yes")
    assert code == 0
    current = cli(db, "current")[1]
    assert current["current"] == []  # replaced records removed too: not "applied" again
    assert [p["revision"] for p in current["pending"]] == [s, "r5"]


def test_running_a_squash_on_a_database_with_data_asks(project: Path, dbs: Any) -> None:
    db = dbs("data")
    db["users"].insert_one({"email": "existing"})
    squash(project)
    code, data = cli(db, "upgrade")
    assert code == 1
    assert "builds the schema" in data["error"]["message"]
    assert "users" in data["error"]["message"]
    assert cli(db, "upgrade", "--yes")[0] == 0
