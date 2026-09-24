"""Integration fixtures. Needs MongoDB: `docker compose up -d --wait`.

Tests are skipped when MongoDB is unreachable, unless MONGOMIG_REQUIRE_MONGO=1 (set in CI),
in which case they fail instead of silently passing.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Iterator
from typing import Any

import pytest
from pymongo import MongoClient
from pymongo.database import Database
from pymongo.errors import PyMongoError

DEFAULT_URI = "mongodb://localhost:27017/?directConnection=true"


@pytest.fixture(scope="session")
def mongo_uri() -> str:
    return os.environ.get("MONGOMIG_TEST_URI", DEFAULT_URI)


@pytest.fixture(scope="session")
def mongo_client(mongo_uri: str) -> Iterator[MongoClient[dict[str, Any]]]:
    client: MongoClient[dict[str, Any]] = MongoClient(mongo_uri, serverSelectionTimeoutMS=2000)
    try:
        client.admin.command("ping")
    except PyMongoError as exc:
        client.close()
        if os.environ.get("MONGOMIG_REQUIRE_MONGO") == "1":
            pytest.fail(f"MongoDB not reachable at {mongo_uri}: {exc}")
        pytest.skip("MongoDB not reachable (run `docker compose up -d --wait`)")
    yield client
    client.close()


@pytest.fixture
def db_name() -> str:
    return f"mongomig_test_{secrets.token_hex(4)}"


@pytest.fixture
def mongo_db(
    mongo_client: MongoClient[dict[str, Any]], db_name: str
) -> Iterator[Database[dict[str, Any]]]:
    yield mongo_client[db_name]
    mongo_client.drop_database(db_name)
