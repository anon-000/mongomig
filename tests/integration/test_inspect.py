from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pymongo.database import Database
from typer.testing import CliRunner

from mongomig.cli.app import app
from mongomig.schema.inference import inspect_collection, user_collections

pytestmark = pytest.mark.integration
runner = CliRunner()


@pytest.fixture
def seeded(mongo_db: Database[dict[str, Any]]) -> Database[dict[str, Any]]:
    users = mongo_db["users"]
    docs: list[dict[str, Any]] = []
    for i in range(300):
        doc: dict[str, Any] = {"name": f"u{i}", "email": f"u{i}@x", "at": datetime.now(UTC)}
        if i % 3 == 0:
            doc["age"] = "27" if i % 30 == 0 else i
        if i % 2 == 0:
            doc["profile"] = {"verified": bool(i % 4)}
        docs.append(doc)
    users.insert_many(docs)
    users.create_index("email", unique=True, name="users_email_unique")
    mongo_db.command(
        "collMod",
        "users",
        validator={"$jsonSchema": {"required": ["email"]}},
        validationLevel="moderate",
    )
    mongo_db["orders"].insert_one({"total": 1.5})
    mongo_db["__mongomig_migrations"].insert_one({"_id": "x"})
    return mongo_db


def test_inspect_collection_full(seeded: Database[dict[str, Any]]) -> None:
    result = inspect_collection(seeded, "users", sample_size=10_000)
    assert result.is_complete  # sample bigger than the collection → everything read
    assert result.documents_scanned == 300
    fields = result.schema.fields
    assert fields["name"].required
    age = fields["age"]
    assert age.stats is not None
    assert age.stats.presence == pytest.approx(100 / 300)
    assert set(age.bson_types) == {"int", "string"}
    assert fields["at"].bson_types == ("date",)
    assert fields["profile"].fields is not None
    assert {ix.name for ix in result.schema.indexes} == {"_id_", "users_email_unique"}
    assert result.schema.validator == {"$jsonSchema": {"required": ["email"]}}
    assert result.schema.validation_level == "moderate"


def test_inspect_sampling_modes(seeded: Database[dict[str, Any]]) -> None:
    sampled = inspect_collection(seeded, "users", sample_size=50)
    assert sampled.documents_scanned == 50
    assert sampled.mode == "sample"
    assert not sampled.is_complete

    percent = inspect_collection(seeded, "users", sample_percent=10)
    assert percent.documents_scanned == 30
    assert percent.mode == "percent"

    full = inspect_collection(seeded, "users", sample_size=5, full_scan=True)
    assert full.documents_scanned == 300
    assert full.is_complete


def test_user_collections_skip_internal(seeded: Database[dict[str, Any]]) -> None:
    assert user_collections(seeded) == ["orders", "users"]


def test_inspect_cli(
    seeded: Database[dict[str, Any]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mongo_uri: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])
    monkeypatch.setenv("MONGODB_URI", mongo_uri)
    monkeypatch.setenv("MONGODB_DATABASE", seeded.name)

    result = runner.invoke(app, ["inspect", "users", "--sample-size", "100"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "Collection: users" in out
    assert "100 random sample of ~300" in out
    assert "users_email_unique (email ↑, unique)" in out
    assert "Validator: yes" in out
    assert "--full-scan" in out

    data = json.loads(runner.invoke(app, ["--json", "inspect"]).stdout)
    assert [c["name"] for c in data["collections"]] == ["orders", "users"]
    assert data["collections"][1]["complete"] is True

    missing = runner.invoke(app, ["inspect", "nope"])
    assert missing.exit_code == 1
    assert "not found" in missing.output

    conflict = runner.invoke(app, ["inspect", "--full-scan", "--sample-size", "5"])
    assert conflict.exit_code == 1
