"""Generated migrations must actually run: autogenerate → upgrade → downgrade on real data."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pymongo.database import Database

from mongomig.metadata import registry
from tests.project import Project

pytestmark = pytest.mark.integration


@pytest.fixture
def project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mongo_uri: str,
    mongo_db: Database[dict[str, Any]],
) -> Iterator[Project]:
    monkeypatch.setenv("MONGODB_URI", mongo_uri)
    monkeypatch.setenv("MONGODB_DATABASE", mongo_db.name)
    yield Project(tmp_path, monkeypatch)
    registry._default = None


def test_generated_migrations_run_both_ways(
    project: Project, mongo_db: Database[dict[str, Any]]
) -> None:
    mongo_db["users"].insert_many(
        [{"first_name": f"n{i}", "email": f"u{i}@x", "legacy": True} for i in range(250)]
    )
    project.models("""
@collection("users", indexes=[Index("email")])
class User(BaseModel):
    first_name: str
    email: str
    legacy: bool | None = None
""")
    assert project.run("baseline").exit_code == 0
    assert project.run("upgrade").exit_code == 0

    project.models("""
class Profile(BaseModel):
    verified: bool = False

@collection("users", indexes=[Index("email", unique=True, name="users_email_unique")],
            validator="auto")
class User(BaseModel):
    given_name: str
    email: str
    status: str = "active"
    profile: Profile = Field(default_factory=Profile)

@collection("orders", indexes=[Index([("user_id", 1), ("at", -1)])])
class Order(BaseModel):
    user_id: str
    at: datetime.datetime
""")
    gen = project.run(
        "revision", "--autogenerate", "-m", "evolve", "--rename", "users.first_name:given_name"
    )
    assert gen.exit_code == 0, gen.output

    up = project.run("upgrade")
    assert up.exit_code == 0, up.output
    users = mongo_db["users"]
    assert users.count_documents({"given_name": {"$exists": True}}) == 250
    assert users.count_documents({"first_name": {"$exists": True}}) == 0
    assert users.count_documents({"status": "active"}) == 250
    assert users.count_documents({"legacy": True}) == 250  # removed from model, data kept
    assert "users_email_unique" in users.index_information()
    assert "email_1" not in users.index_information()
    assert "user_id_1_at_-1" in mongo_db["orders"].index_information()
    info = next(mongo_db.list_collections(filter={"name": "users"}))
    assert info["options"]["validator"]["$jsonSchema"]["required"][:2] == ["_id", "given_name"]
    assert project.run("diff", "--check").exit_code == 0

    down = project.run("downgrade", "--yes")
    assert down.exit_code == 0, down.output
    assert users.count_documents({"first_name": {"$exists": True}}) == 250
    assert users.count_documents({"status": {"$exists": True}}) == 0
    assert "email_1" in users.index_information()
    assert "users_email_unique" not in users.index_information()
    info = next(mongo_db.list_collections(filter={"name": "users"}))
    assert not info["options"].get("validator")


def test_profile_object_with_factory_default_needs_review(project: Project) -> None:
    # Field(default_factory=Profile) is a runtime default: autogenerate must not guess it.
    project.models("""
@collection("users")
class User(BaseModel):
    email: str
""")
    project.run("revision", "--autogenerate", "-m", "initial")
    project.models("""
class Profile(BaseModel):
    verified: bool = False

@collection("users")
class User(BaseModel):
    email: str
    profile: Profile = Field(default_factory=Profile)
""")
    result = project.json("revision", "--autogenerate", "-m", "profile")
    assert result["review"] == [
        "users.profile: default is computed at runtime: choose a value for existing documents"
    ]


def test_beanie_models_autogenerate_indexes(
    project: Project, mongo_db: Database[dict[str, Any]]
) -> None:
    project.models("""
import pymongo
from typing import Annotated
from beanie import Document, Indexed

class Product(Document):
    sku: Indexed(str, unique=True)
    title: Annotated[str, Indexed()]
    price: float

    class Settings:
        name = "products"
        indexes = [pymongo.IndexModel([("price", 1), ("sku", -1)], name="price_sku")]

MongoMetadata.default().register_beanie(Product)
""")
    assert project.run("revision", "--autogenerate", "-m", "products").exit_code == 0
    up = project.run("upgrade")
    assert up.exit_code == 0, up.output
    indexes = mongo_db["products"].index_information()
    assert {"sku_1", "title_1", "price_sku"} <= set(indexes)
    assert indexes["sku_1"]["unique"] is True
