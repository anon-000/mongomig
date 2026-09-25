"""End-to-end: real revision files, run through the CLI / API against MongoDB."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

import pytest
from pymongo.database import Database
from typer.testing import CliRunner

from mongomig import aupgrade_to_head, upgrade_to_head
from mongomig.cli.app import app
from mongomig.migrations.lock import MigrationLock
from tests.helpers import write_migration

pytestmark = pytest.mark.integration
runner = CliRunner()

TRACKING = "__mongomig_migrations"


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


@pytest.fixture
def versions(project: Path) -> Path:
    return project / "migrations" / "versions"


def cli(*args: str) -> tuple[int, str]:
    result = runner.invoke(app, list(args))
    return result.exit_code, result.output


def cli_json(*args: str) -> Any:
    result = runner.invoke(app, ["--json", *args])
    return json.loads(result.stdout)


def applied(db: Database[dict[str, Any]]) -> set[str]:
    return {d["_id"] for d in db[TRACKING].find({"status": "applied"})}


def three_step_project(versions: Path) -> None:
    write_migration(
        versions,
        "r1",
        upgrade='ctx.ops.create_index("users", "email", unique=True, name="users_email_unique")',
        downgrade='ctx.ops.drop_index("users", "users_email_unique")',
        minute=0,
    )
    write_migration(
        versions,
        "r2",
        "r1",
        upgrade='ctx.ops.backfill("users", {"status": {"$exists": False}}, '
        '{"$set": {"status": "active"}})',
        downgrade='ctx.ops.unset_field("users", "status")',
        minute=1,
    )
    write_migration(
        versions,
        "r3",
        "r2",
        upgrade='ctx.collection("audit").insert_one({"event": "r3"})',
        downgrade='ctx.collection("audit").delete_many({"event": "r3"})',
        minute=2,
    )


def test_upgrade_and_downgrade_roundtrip(
    versions: Path, mongo_db: Database[dict[str, Any]]
) -> None:
    three_step_project(versions)
    mongo_db["users"].insert_many([{"email": f"u{i}@x"} for i in range(5)])

    code, output = cli("upgrade")
    assert code == 0, output
    assert "Running upgrade <base> -> r1" in output
    assert "Applied 3 revision(s)" in output
    assert applied(mongo_db) == {"r1", "r2", "r3"}
    assert mongo_db["users"].count_documents({"status": "active"}) == 5
    assert "users_email_unique" in mongo_db["users"].index_information()

    record = mongo_db[TRACKING].find_one({"_id": "r2"})
    assert record is not None
    assert record["checksum"].startswith("sha256:")
    assert record["execution_time_ms"] >= 0
    assert record["meta"]["hostname"]

    assert cli_json("current")["up_to_date"] is True
    code, _ = cli("current", "--check")
    assert code == 0

    code, output = cli("upgrade")
    assert code == 0
    assert "Already up to date" in output

    # one step back (default), then to base
    code, output = cli("downgrade", "--yes")
    assert code == 0, output
    assert applied(mongo_db) == {"r1", "r2"}
    assert mongo_db["audit"].count_documents({}) == 0

    code, output = cli("downgrade", "base", "--yes")
    assert code == 0, output
    assert applied(mongo_db) == set()
    assert mongo_db["users"].count_documents({"status": {"$exists": True}}) == 0
    assert "users_email_unique" not in mongo_db["users"].index_information()


def test_upgrade_to_revision_and_steps(versions: Path, mongo_db: Database[dict[str, Any]]) -> None:
    three_step_project(versions)
    assert cli_json("upgrade", "r1")["applied"][0]["revision"] == "r1"
    assert applied(mongo_db) == {"r1"}
    assert [s["revision"] for s in cli_json("upgrade", "--steps", "1")["applied"]] == ["r2"]
    assert applied(mongo_db) == {"r1", "r2"}

    data = cli_json("current")
    assert data["up_to_date"] is False
    code, output = cli("current", "--check")
    assert code == 1
    assert "1 pending" in output
    result = runner.invoke(app, ["--json", "current", "--check"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["pending"][0]["revision"] == "r3"  # single JSON doc


def test_downgrade_to_revision_keeps_it(versions: Path, mongo_db: Database[dict[str, Any]]) -> None:
    three_step_project(versions)
    cli("upgrade")
    reverted = cli_json("downgrade", "r1", "--yes")["reverted"]
    assert [r["revision"] for r in reverted] == ["r3", "r2"]
    assert applied(mongo_db) == {"r1"}


def test_downgrade_requires_confirmation(
    versions: Path, mongo_db: Database[dict[str, Any]]
) -> None:
    three_step_project(versions)
    cli("upgrade")
    code, output = cli("downgrade")  # CliRunner stdin is not a TTY
    assert code == 1
    assert "--yes" in output
    assert applied(mongo_db) == {"r1", "r2", "r3"}


def test_failed_migration_is_recorded_and_retryable(
    versions: Path, mongo_db: Database[dict[str, Any]]
) -> None:
    write_migration(versions, "ok1", upgrade='ctx.collection("t").insert_one({"a": 1})', minute=0)
    bad = write_migration(
        versions,
        "bad2",
        "ok1",
        upgrade='ctx.ops.backfill("t", {}, {"$set": {"x": 1}})\nraise RuntimeError("kaboom")',
        minute=1,
    )
    write_migration(versions, "later3", "bad2", minute=2)

    result = runner.invoke(app, ["--json", "upgrade"])
    assert result.exit_code == 2
    err = json.loads(result.stdout)["error"]
    assert err["type"] == "MigrationExecutionError"
    assert "kaboom" in err["message"]
    assert err["details"]["revision"] == "bad2"

    assert applied(mongo_db) == {"ok1"}  # later3 never ran
    record = mongo_db[TRACKING].find_one({"_id": "bad2"})
    assert record is not None
    assert record["status"] == "failed"
    assert "kaboom" in record["error"]

    current = cli_json("current")
    assert current["failed"] == ["bad2"]
    assert [p["revision"] for p in current["pending"]] == ["bad2", "later3"]

    bad.write_text(bad.read_text().replace('raise RuntimeError("kaboom")', "pass"))
    code, output = cli("upgrade")
    assert code == 0, output
    assert applied(mongo_db) == {"ok1", "bad2", "later3"}
    assert cli_json("current")["failed"] == []


def test_error_reports_operation_context(
    versions: Path, mongo_db: Database[dict[str, Any]]
) -> None:
    mongo_db["users"].insert_many([{"email": "same"}, {"email": "same"}])
    write_migration(
        versions,
        "idx1",
        upgrade='ctx.ops.create_index("users", "email", unique=True)',
    )
    code, output = cli("upgrade")
    assert code == 2
    assert "create_index on users" in output
    assert "duplicate" in output.lower()


def test_checksum_mismatch_blocks_and_stamp_repairs(
    versions: Path, mongo_db: Database[dict[str, Any]]
) -> None:
    three_step_project(versions)
    cli("upgrade", "r2")
    r1 = next(versions.glob("*_r1.py"))
    r1.write_text(r1.read_text() + "\n# edited after running\n")

    assert cli_json("current")["modified"] == ["r1"]
    code, output = cli("upgrade")
    assert code == 6
    assert "modified after they ran" in output
    assert "mongomig stamp r2" in output

    code, output = cli("stamp", "r2")
    assert code == 0, output
    assert cli_json("current")["modified"] == []
    code, _ = cli("upgrade")
    assert code == 0
    assert applied(mongo_db) == {"r1", "r2", "r3"}


def test_stamp_base_and_head(versions: Path, mongo_db: Database[dict[str, Any]]) -> None:
    three_step_project(versions)
    assert cli_json("stamp", "head")["stamped"] == ["r1", "r2", "r3"]
    assert applied(mongo_db) == {"r1", "r2", "r3"}
    assert mongo_db["audit"].count_documents({}) == 0  # nothing actually ran
    assert cli_json("stamp", "base")["stamped"] == []
    assert applied(mongo_db) == set()


def test_irreversible_downgrade(versions: Path, mongo_db: Database[dict[str, Any]]) -> None:
    write_migration(versions, "a1", minute=0)
    write_migration(
        versions,
        "drop2",
        "a1",
        upgrade='ctx.ops.drop_collection("legacy")',
        downgrade=None,
        reversible=False,
        minute=1,
    )
    cli("upgrade")
    code, output = cli("downgrade", "base", "--yes")
    assert code == 2
    assert "reversible = False" in output
    assert applied(mongo_db) == {"a1", "drop2"}

    code, output = cli("downgrade", "base", "--yes", "--force")
    assert code == 0, output
    assert "no downgrade()" in output
    assert applied(mongo_db) == set()


def test_multiple_heads_need_merge(versions: Path, mongo_db: Database[dict[str, Any]]) -> None:
    write_migration(versions, "base0", minute=0)
    write_migration(versions, "left1", "base0", minute=1)
    write_migration(versions, "right2", "base0", minute=2)

    code, output = cli("upgrade")
    assert code == 4
    assert "Multiple heads" in output

    code, _ = cli("upgrade", "heads")
    assert code == 0
    assert applied(mongo_db) == {"base0", "left1", "right2"}

    merge_rev = cli_json("merge", "-m", "join")["revision"]
    code, _ = cli("upgrade")
    assert code == 0
    assert merge_rev in applied(mongo_db)
    assert [c["revision"] for c in cli_json("current")["current"]] == [merge_rev]


def test_lock_held_by_other_runner(versions: Path, mongo_db: Database[dict[str, Any]]) -> None:
    write_migration(versions, "a1")
    other = MigrationLock(mongo_db, "__mongomig_lock", owner="deploy-job-7")
    other.acquire()
    try:
        code, output = cli("upgrade")
        assert code == 5
        assert "deploy-job-7" in output
        assert applied(mongo_db) == set()
    finally:
        other.release()
    code, _ = cli("upgrade")
    assert code == 0


def test_unknown_revisions_warn(versions: Path, mongo_db: Database[dict[str, Any]]) -> None:
    write_migration(versions, "a1")
    mongo_db[TRACKING].insert_one({"_id": "from_newer_code", "status": "applied"})
    code, output = cli("upgrade")
    assert code == 0
    assert "no file here: from_newer_code" in output


def test_migration_can_import_project_code(project: Path, versions: Path) -> None:
    (project / "myapp_helpers.py").write_text("DEFAULT_STATUS = 'active'\n")
    write_migration(
        versions,
        "imp1",
        upgrade="from myapp_helpers import DEFAULT_STATUS\n"
        'ctx.collection("t").insert_one({"s": DEFAULT_STATUS})',
    )
    code, output = cli("upgrade")
    assert code == 0, output


def test_concurrent_workers_run_each_migration_once(
    versions: Path, mongo_db: Database[dict[str, Any]]
) -> None:
    """Simulates N uvicorn workers all calling upgrade_to_head() at startup."""
    write_migration(
        versions,
        "slow1",
        upgrade='import time\ntime.sleep(0.5)\nctx.collection("runs").insert_one({"rev": "slow1"})',
    )
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            upgrade_to_head(lock_timeout=10)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert mongo_db["runs"].count_documents({}) == 1
    assert applied(mongo_db) == {"slow1"}


def test_async_helper(versions: Path, mongo_db: Database[dict[str, Any]]) -> None:
    write_migration(versions, "a1", upgrade='ctx.collection("t").insert_one({})')
    result = asyncio.run(aupgrade_to_head())
    assert result.revisions == ["a1"]
    assert asyncio.run(aupgrade_to_head()).revisions == []
