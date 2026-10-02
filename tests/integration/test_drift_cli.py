from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pymongo.database import Database

from mongomig import check_drift
from mongomig.metadata import registry
from tests.project import Project

pytestmark = pytest.mark.integration

MODELS = """
@collection("users", indexes=[Index("email", unique=True, name="users_email_unique")])
class User(BaseModel):
    email: str
    age: int | None = None
    status: str = "active"

@collection("orders")
class Order(BaseModel):
    total: float
"""


@pytest.fixture
def project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mongo_uri: str,
    mongo_db: Database[dict[str, Any]],
) -> Iterator[Project]:
    monkeypatch.setenv("MONGODB_URI", mongo_uri)
    monkeypatch.setenv("MONGODB_DATABASE", mongo_db.name)
    p = Project(tmp_path, monkeypatch)
    p.models(MODELS)
    yield p
    registry._default = None


def seed(db: Database[dict[str, Any]], *, bad_age: int = 0, missing_status: int = 0) -> None:
    docs: list[dict[str, Any]] = []
    for i in range(200):
        doc: dict[str, Any] = {"email": f"u{i}@x", "age": i, "status": "active"}
        if i < bad_age:
            doc["age"] = str(i)
        if 100 <= i < 100 + missing_status:
            del doc["status"]
        docs.append(doc)
    db["users"].insert_many(docs)
    db["users"].create_index("email", unique=True, name="users_email_unique")
    db["orders"].insert_one({"total": 1.5})


def test_clean_database(project: Project, mongo_db: Database[dict[str, Any]]) -> None:
    seed(mongo_db)
    result = project.run("drift", "--check")
    assert result.exit_code == 0, result.output
    assert "✓ matches the model" in result.output
    assert "all 200 documents" in result.output


def test_drift_found_and_check_fails(project: Project, mongo_db: Database[dict[str, Any]]) -> None:
    seed(mongo_db, bad_age=10, missing_status=1)  # 5% strings; 0.5% missing (== threshold)
    mongo_db["users"].insert_one({"email": "x@x", "age": 1, "status": "a", "legacy": True})

    data = project.json("drift")
    users = next(c for c in data["collections"] if c["name"] == "users")
    by_kind = {(f["kind"], f["path"]): f for f in users["findings"]}
    assert by_kind[("unexpected_type", "age")]["status"] == "fail"
    assert by_kind[("missing_field", "status")]["status"] == "warn"  # 1 of 201: below 0.5%
    assert by_kind[("unexpected_field", "legacy")]["status"] == "warn"
    assert data["failed"] == 1

    assert project.run("drift", "--check").exit_code == 1
    strict = project.json("drift", "--strict")
    assert strict["failed"] == 3  # zero tolerance: every finding fails


def test_structural_drift(project: Project, mongo_db: Database[dict[str, Any]]) -> None:
    mongo_db["users"].insert_one({"email": "a", "age": 1, "status": "a"})  # no index, no orders
    data = project.json("drift")
    kinds = {f["kind"] for c in data["collections"] for f in c["findings"]}
    assert kinds == {"index_missing", "collection_missing"}
    result = project.run("drift")
    assert "(index)" in result.output
    assert "(collection)" in result.output


def test_options_and_errors(project: Project, mongo_db: Database[dict[str, Any]]) -> None:
    seed(mongo_db, bad_age=5)
    only_orders = project.json("drift", "orders")
    assert [c["name"] for c in only_orders["collections"]] == ["orders"]
    sampled = project.json("drift", "users", "--sample-size", "50")
    assert sampled["collections"][0]["documents_scanned"] == 50
    full = project.json("drift", "users", "--full-scan")
    assert full["collections"][0]["complete"] is True

    unknown = project.run("drift", "nope")
    assert unknown.exit_code == 1
    assert "Not a registered collection" in unknown.output
    assert project.run("drift", "--full-scan", "--sample-size", "5").exit_code == 1


def test_pending_migrations_warning(project: Project, mongo_db: Database[dict[str, Any]]) -> None:
    seed(mongo_db)
    project.run("revision", "-m", "not applied yet")
    result = project.run("drift")
    assert "1 migration(s) pending" in result.output


def test_thresholds_from_config(project: Project, mongo_db: Database[dict[str, Any]]) -> None:
    seed(mongo_db, bad_age=10)  # 5% strings
    config = project.root / "mongomig.yaml"
    config.write_text(
        config.read_text().replace("unexpected_type_percent: 0.5", "unexpected_type_percent: 10")
    )
    assert project.run("drift", "--check").exit_code == 0


def test_python_api(project: Project, mongo_db: Database[dict[str, Any]]) -> None:
    seed(mongo_db, bad_age=10)
    registry._default = None
    results = {r.name: r for r in check_drift()}
    assert results["orders"].findings == []
    assert [f.path for f in results["users"].failed] == ["age"]
