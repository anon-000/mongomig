from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pymongo.database import Database
from typer.testing import CliRunner

import mongomig
from mongomig.cli.app import app
from mongomig.errors import RevisionNotFoundError
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
    runner.invoke(app, ["init"])
    monkeypatch.setenv("MONGODB_URI", mongo_uri)
    monkeypatch.setenv("MONGODB_DATABASE", mongo_db.name)
    versions = tmp_path / "migrations" / "versions"
    write_migration(
        versions,
        "aaaa1111",
        message="create things",
        minute=0,
        upgrade='ctx.collection("things").insert_one({"n": 1})',
    )
    write_migration(
        versions,
        "bbbb2222",
        "aaaa1111",
        message="split names",
        minute=1,
        upgrade='ctx.collection("things").update_many({}, {"$set": {"split": True}})',
    )
    return tmp_path


def test_current_state(project: Path) -> None:
    assert mongomig.current_state().pending == ["aaaa1111", "bbbb2222"]
    mongomig.upgrade("aaaa1111")
    state = mongomig.current_state()
    assert (state.applied_heads, state.pending, state.failed) == (["aaaa1111"], ["bbbb2222"], [])


def test_load_revision(project: Path, mongo_db: Database[dict[str, Any]]) -> None:
    by_name = mongomig.load_revision("bbbb2222")
    assert by_name.revision == "bbbb2222"
    assert mongomig.load_revision("bbbb").revision == "bbbb2222"  # id prefix
    mongo_db["things"].insert_one({"n": 2})
    by_name.upgrade(mongomig.MigrationContext(mongo_db))
    assert mongo_db["things"].count_documents({"split": True}) == 1
    with pytest.raises(RevisionNotFoundError):
        mongomig.load_revision("nothing_like_this")
