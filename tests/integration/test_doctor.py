from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pymongo import MongoClient
from pymongo.database import Database
from typer.testing import CliRunner

from mongomig.cli.app import app
from mongomig.safety.impact import is_sharded
from tests.helpers import write_migration

pytestmark = pytest.mark.integration
runner = CliRunner()


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


def doctor() -> tuple[int, dict[str, list[dict[str, str]]]]:
    result = runner.invoke(app, ["--json", "doctor"])
    data = json.loads(result.stdout)
    by_name: dict[str, list[dict[str, str]]] = {}
    for check in data["checks"]:
        by_name.setdefault(check["name"], []).append(check)
    return result.exit_code, by_name


def test_doctor_healthy(project: Path) -> None:
    code, checks = doctor()
    assert code == 0
    for name in (
        "versions",
        "configuration",
        "connection",
        "server version",
        "topology",
        "tracking",
        "lock",
    ):
        assert checks[name][0]["status"] == "ok", name
    assert "replica set" in checks["topology"][0]["detail"]
    assert checks["permissions"][0]["status"] == "skip"  # local server: no auth


def test_doctor_reports_problems(project: Path, mongo_db: Database[dict[str, Any]]) -> None:
    mongo_db["__mongomig_migrations"].insert_one({"_id": "x1", "status": "failed"})
    mongo_db["__mongomig_lock"].insert_one(
        {
            "_id": "migration",
            "owner": "deploy-7",
            "expires_at": datetime.now(UTC) + timedelta(minutes=5),
        }
    )
    mongo_db["__mongomig_backup_r1"].insert_one({"v": 1})
    code, checks = doctor()
    assert code == 1
    assert checks["tracking"][0]["status"] == "fail"
    assert checks["lock"][0]["status"] == "warn"
    assert "deploy-7" in checks["lock"][0]["detail"]
    assert checks["backups"][0]["status"] == "warn"


def test_doctor_without_connection_string(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MONGODB_URI")
    code, checks = doctor()
    assert code == 1
    assert checks["connection string"][0]["status"] == "fail"
    assert checks["connection"][0]["status"] == "skip"


@pytest.fixture
def users(
    mongo_client: MongoClient[dict[str, Any]], mongo_db: Database[dict[str, Any]]
) -> Iterator[Database[dict[str, Any]]]:
    roles = {
        "reader": ["read"],
        "writer": ["readWrite"],
        "admin_writer": ["readWrite", "dbAdmin"],
    }
    for user, role_names in roles.items():
        mongo_db.command(
            "createUser",
            user,
            pwd="pw",
            roles=[{"role": r, "db": mongo_db.name} for r in role_names],
        )
    yield mongo_db
    for user in roles:
        mongo_db.command("dropUser", user)


@pytest.mark.parametrize(
    ("user", "status", "warnings"),
    [("reader", "fail", 3), ("writer", "ok", 1), ("admin_writer", "ok", 0)],
)
def test_doctor_permissions(
    project: Path,
    users: Database[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    user: str,
    status: str,
    warnings: int,
) -> None:
    monkeypatch.setenv(
        "MONGODB_URI",
        f"mongodb://{user}:pw@localhost:27017/{users.name}?directConnection=true"
        f"&authSource={users.name}",
    )
    _, checks = doctor()
    perms = checks["permissions"]
    assert perms[0]["status"] == status
    assert sum(1 for c in perms if c["status"] == "warn") == warnings
    if user == "writer":
        assert "collMod" in perms[1]["detail"]
    assert "pw@" not in json.dumps(checks)  # never leak credentials


def test_validate_with_database(project: Path, mongo_db: Database[dict[str, Any]]) -> None:
    versions = project / "migrations" / "versions"
    write_migration(versions, "a1")
    assert runner.invoke(app, ["upgrade"]).exit_code == 0
    assert runner.invoke(app, ["validate", "--database"]).exit_code == 0

    path = next(versions.glob("*a1.py"))
    path.write_text(path.read_text() + "# edited\n")
    mongo_db["__mongomig_migrations"].insert_one({"_id": "ghost", "status": "applied"})
    result = runner.invoke(app, ["--json", "validate", "--database"])
    assert result.exit_code == 1
    checks = {c["name"]: c["status"] for c in json.loads(result.stdout)["checks"]}
    assert checks["applied checksums"] == "fail"
    assert checks["unknown revisions"] == "warn"


def test_is_sharded_false_on_replica_set(mongo_db: Database[dict[str, Any]]) -> None:
    mongo_db["users"].insert_one({})
    assert is_sharded(mongo_db, "users") is False
