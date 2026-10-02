"""Resumable migrations: crash mid-way, re-run, check exactly what was processed twice."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from bson import ObjectId
from pymongo.database import Database
from typer.testing import CliRunner

from mongomig.cli.app import app
from mongomig.errors import TransactionsUnsupportedError
from mongomig.migrations.batching import id_ordered_batches
from mongomig.migrations.checkpoints import CheckpointStore
from mongomig.migrations.context import MigrationContext, require_transactions
from mongomig.migrations.dryrun import Recorder
from tests.helpers import write_migration

pytestmark = pytest.mark.integration
runner = CliRunner()


class Crash(Exception):
    pass


def store(db: Database[dict[str, Any]], checksum: str = "sha256:a") -> CheckpointStore:
    return CheckpointStore(
        db, "__mongomig_checkpoints", revision="r1", direction="upgrade", checksum=checksum
    )


def ctx_crashing_after(
    db: Database[dict[str, Any]], batches: int | None, checksum: str = "sha256:a"
) -> MigrationContext:
    ctx = MigrationContext(db, batch_size=10, revision="r1", checkpoints=store(db, checksum))
    calls = {"n": 0}

    def check_lock() -> None:  # called before every batch: a convenient crash point
        calls["n"] += 1
        if batches is not None and calls["n"] > batches:
            raise Crash

    ctx.check_lock = check_lock  # type: ignore[method-assign]
    return ctx


def test_ops_resume_from_checkpoint(mongo_db: Database[dict[str, Any]]) -> None:
    mongo_db["t"].insert_many([{"n": 0} for _ in range(100)])

    with pytest.raises(Crash):
        ctx_crashing_after(mongo_db, 3).ops.backfill("t", {}, {"$inc": {"n": 1}})
    assert mongo_db["t"].count_documents({"n": 1}) == 30

    result = ctx_crashing_after(mongo_db, None).ops.backfill("t", {}, {"$inc": {"n": 1}})
    assert mongo_db["t"].count_documents({"n": 1}) == 100  # each document exactly once
    assert (result.matched, result.batches) == (100, 10)

    again = ctx_crashing_after(mongo_db, None).ops.backfill("t", {}, {"$inc": {"n": 1}})
    assert again.matched == 100  # completed checkpoint: skipped, not re-applied
    assert mongo_db["t"].count_documents({"n": 1}) == 100


def test_changed_operation_or_file_starts_over(mongo_db: Database[dict[str, Any]]) -> None:
    mongo_db["t"].insert_many([{"n": 0} for _ in range(50)])
    with pytest.raises(Crash):
        ctx_crashing_after(mongo_db, 2).ops.backfill("t", {}, {"$inc": {"n": 1}})

    # different update → different fingerprint → checkpoint ignored
    other = ctx_crashing_after(mongo_db, None).ops.backfill("t", {}, {"$set": {"m": 1}})
    assert other.matched == 50

    with pytest.raises(Crash):
        ctx_crashing_after(mongo_db, 2).ops.backfill("t", {}, {"$set": {"k": 1}})
    s = store(mongo_db, checksum="sha256:b")  # the migration file was edited
    assert s.discard_stale() >= 1
    redo = MigrationContext(mongo_db, batch_size=10, revision="r1", checkpoints=s)
    assert redo.ops.backfill("t", {}, {"$set": {"k": 1}}).matched == 50


def test_resume_across_mixed_id_types(mongo_db: Database[dict[str, Any]]) -> None:
    ids: list[Any] = [3, 1, 2.5, "b", "a", ObjectId(), None]
    mongo_db["t"].insert_many([{"_id": i} for i in ids])
    seen = [d["_id"] for batch in id_ordered_batches(mongo_db["t"], {}, 2) for d in batch]
    assert seen[:5] == [None, 1, 2.5, 3, "a"]
    resumed = [
        d["_id"] for b in id_ordered_batches(mongo_db["t"], {}, 2, start_after=2.5) for d in b
    ]
    assert resumed[:3] == [3, "a", "b"]
    assert len(resumed) == 4  # 3, "a", "b", ObjectId: nothing after a type change is skipped


def test_transaction_helper(mongo_db: Database[dict[str, Any]]) -> None:
    ctx = MigrationContext(mongo_db)
    mongo_db.create_collection("t")
    with ctx.transaction() as session:
        mongo_db["t"].insert_one({"a": 1}, session=session)
    assert mongo_db["t"].count_documents({}) == 1

    def failing_write() -> None:
        with ctx.transaction() as session:
            mongo_db["t"].insert_one({"a": 2}, session=session)
            raise Crash

    with pytest.raises(Crash):
        failing_write()
    assert mongo_db["t"].count_documents({}) == 1  # rolled back

    dry = MigrationContext(mongo_db, recorder=Recorder())
    with dry.transaction() as session:
        assert session is None


def test_standalone_server_is_rejected() -> None:
    class FakeAdmin:
        def command(self, name: str) -> dict[str, Any]:
            return {"isWritablePrimary": True}

    class FakeClient:
        admin = FakeAdmin()

    with pytest.raises(TransactionsUnsupportedError, match="replica set"):
        require_transactions(FakeClient())  # type: ignore[arg-type]


def test_batches_in_dry_run_process_only_the_first_batch(
    mongo_db: Database[dict[str, Any]],
) -> None:
    mongo_db["t"].insert_many([{"n": i} for i in range(25)])
    rec = Recorder()
    ctx = MigrationContext(mongo_db, batch_size=10, recorder=rec)
    batches = list(ctx.batches("t", {"n": {"$gte": 5}}))
    assert [len(b) for b in batches] == [10]
    assert rec.ops[0].operation == "batches"
    assert rec.ops[0].estimated_docs == 20


# --- end to end through the CLI ----------------------------------------------------------

LOOP = """
import os
coll = ctx.collection("t")
for batch in ctx.batches("t", batch_size=10{extra}):
    for doc in batch:
        coll.update_one({{"_id": doc["_id"]}}, {{"$inc": {{"n": 1}}}}{session})
    if os.path.exists("CRASH") and batch.number == 3:
        raise RuntimeError("crash in batch 3")
"""


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
    mongo_db["t"].insert_many([{"n": 0} for _ in range(100)])
    return tmp_path


def counts(db: Database[dict[str, Any]]) -> dict[int, int]:
    return {
        d["_id"]: d["c"] for d in db["t"].aggregate([{"$group": {"_id": "$n", "c": {"$sum": 1}}}])
    }


def test_crash_current_and_resume(project: Path, mongo_db: Database[dict[str, Any]]) -> None:
    write_migration(
        project / "migrations" / "versions", "loop1", upgrade=LOOP.format(extra="", session="")
    )
    (project / "CRASH").touch()
    failed = runner.invoke(app, ["upgrade"])
    assert failed.exit_code == 2
    assert "batches on t after 20 documents" in failed.output

    current = runner.invoke(app, ["current"])
    assert "resumes from checkpoint: t (20 documents done)" in current.output
    assert "mongomig resume" in current.output
    assert json.loads(runner.invoke(app, ["--json", "current"]).stdout)["checkpoints"]["loop1"]

    (project / "CRASH").unlink()
    resumed = runner.invoke(app, ["resume"])
    assert resumed.exit_code == 0, resumed.output
    assert "resuming after 20 documents" in resumed.output
    # batch 3 was processed before the crash but not checkpointed: at-least-once
    assert counts(mongo_db) == {1: 90, 2: 10}
    assert mongo_db["__mongomig_checkpoints"].count_documents({}) == 0  # cleared on success
    assert "Nothing to resume" in runner.invoke(app, ["resume"]).output


def test_transactional_batches_are_exactly_once(
    project: Path, mongo_db: Database[dict[str, Any]]
) -> None:
    write_migration(
        project / "migrations" / "versions",
        "loop1",
        upgrade=LOOP.format(extra=", transactional=True", session=", session=batch.session"),
    )
    (project / "CRASH").touch()
    assert runner.invoke(app, ["upgrade"]).exit_code == 2
    assert counts(mongo_db) == {1: 20, 0: 80}  # batch 3 rolled back with its checkpoint
    (project / "CRASH").unlink()
    assert runner.invoke(app, ["resume"]).exit_code == 0
    assert counts(mongo_db) == {1: 100}


def test_editing_a_failed_migration_discards_checkpoints(
    project: Path, mongo_db: Database[dict[str, Any]]
) -> None:
    path = write_migration(
        project / "migrations" / "versions", "loop1", upgrade=LOOP.format(extra="", session="")
    )
    (project / "CRASH").touch()
    assert runner.invoke(app, ["upgrade"]).exit_code == 2
    (project / "CRASH").unlink()
    path.write_text(path.read_text() + "\n# fixed something\n")
    result = runner.invoke(app, ["upgrade"])
    assert result.exit_code == 0, result.output
    assert "checkpoints were discarded and it starts over" in result.output
    assert counts(mongo_db) == {2: 30, 1: 70}
