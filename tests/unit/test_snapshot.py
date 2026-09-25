from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from mongomig import Index, MongoMetadata
from mongomig.errors import ConfigError
from mongomig.schema.indexes import index_from_server
from mongomig.schema.models import CollectionSchema
from mongomig.schema.snapshot import (
    Snapshot,
    canonical_json,
    load_snapshot,
    snapshot_hash,
    write_snapshot,
)


class Profile(BaseModel):
    verified: bool = False


class User(BaseModel):
    name: str
    age: int | None = None
    role: str = "user"
    tags: list[str] = []
    profile: Profile | None = None


def declared() -> tuple[dict[str, CollectionSchema], MongoMetadata]:
    md = MongoMetadata()
    md.register(User, "users", indexes=[Index("name", unique=True)], validator="auto")
    return md.schemas()[0], md


def test_snapshot_roundtrip_is_lossless(tmp_path: Path) -> None:
    schemas, md = declared()
    snap = Snapshot.from_schemas(schemas, md.profile)
    path = tmp_path / "schema_snapshot.json"
    write_snapshot(path, snap)

    loaded = load_snapshot(path)
    assert loaded.to_dict() == snap.to_dict()
    users = loaded.collections["users"]
    assert users.source == "snapshot"
    assert users.fields["role"].default == "user"
    assert users.fields["profile"].fields is not None
    assert users.indexes[0].unique
    assert loaded.storage is not None
    assert loaded.storage["mode"] == "python"


def test_snapshot_file_is_deterministic(tmp_path: Path) -> None:
    schemas, md = declared()
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    write_snapshot(a, Snapshot.from_schemas(schemas, md.profile))
    write_snapshot(b, Snapshot.from_schemas(dict(reversed(schemas.items())), md.profile))
    assert a.read_text() == b.read_text()
    assert snapshot_hash(a) == snapshot_hash(b)

    # hash ignores formatting
    b.write_text(json.dumps(json.loads(a.read_text())))
    assert snapshot_hash(a) == snapshot_hash(b)


def test_missing_invalid_and_future_snapshots(tmp_path: Path) -> None:
    assert load_snapshot(tmp_path / "nope.json").collections == {}
    assert snapshot_hash(tmp_path / "nope.json") is None

    bad = tmp_path / "bad.json"
    bad.write_text("<<<<<<< HEAD\n{}")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_snapshot(bad)

    future = tmp_path / "future.json"
    future.write_text(canonical_json({"mongomig_format": 99, "collections": {}}))
    with pytest.raises(ConfigError, match="format 99"):
        load_snapshot(future)


def test_index_from_server_canonicalises() -> None:
    doc = {
        "v": 2,
        "key": {"email": 1, "created_at": -1.0},
        "name": "email_1_created_at_-1",
        "unique": True,
        "partialFilterExpression": {"deleted": {"$exists": False}},
        "expireAfterSeconds": 3600.0,
    }
    ix = index_from_server(doc)
    assert ix.keys == (("email", 1), ("created_at", -1))
    assert ix.unique
    assert ix.options_dict == {
        "expireAfterSeconds": 3600,
        "partialFilterExpression": {"deleted": {"$exists": False}},
    }
    assert "unique" in ix.describe()
