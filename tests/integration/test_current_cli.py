from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pymongo.database import Database
from typer.testing import CliRunner

from mongomig.cli.app import app

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


def current_json() -> dict[str, Any]:
    result = runner.invoke(app, ["--json", "current"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)  # type: ignore[no-any-return]


def new_rev(message: str) -> str:
    result = runner.invoke(app, ["--json", "revision", "-m", message])
    return json.loads(result.stdout)["revision"]  # type: ignore[no-any-return]


def test_current_fresh_database(project: Path, mongo_db: Database[dict[str, Any]]) -> None:
    r1, r2 = new_rev("one"), new_rev("two")
    data = current_json()
    assert data["database"] == mongo_db.name
    assert data["current"] == []
    assert [p["revision"] for p in data["pending"]] == [r1, r2]
    # `current` is read-only: it must not create the tracking collection
    assert "__mongomig_migrations" not in mongo_db.list_collection_names()


def test_current_partially_applied_and_unknown(
    project: Path, mongo_db: Database[dict[str, Any]]
) -> None:
    r1, r2 = new_rev("one"), new_rev("two")
    now = datetime.now(UTC)
    mongo_db["__mongomig_migrations"].insert_many(
        [
            {"_id": r1, "status": "applied", "applied_at": now},
            {"_id": "from_newer_code", "status": "applied", "applied_at": now},
        ]
    )
    data = current_json()
    assert [c["revision"] for c in data["current"]] == [r1]
    assert [p["revision"] for p in data["pending"]] == [r2]
    assert data["unknown"] == ["from_newer_code"]

    result = runner.invoke(app, ["current"])
    assert "Pending:  1" in result.output
    assert "from_newer_code" in result.output


def test_current_unreachable_database_hides_password(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MONGODB_URI", "mongodb://bob:s3cr3t@127.0.0.1:1/?directConnection=true")
    (project / "mongomig.yaml").write_text(
        (project / "mongomig.yaml")
        .read_text()
        .replace("server_selection_timeout_ms: 5000", "server_selection_timeout_ms: 300")
    )
    result = runner.invoke(app, ["-v", "current"])
    assert result.exit_code == 2
    assert "Cannot connect" in result.output
    assert "s3cr3t" not in result.output
