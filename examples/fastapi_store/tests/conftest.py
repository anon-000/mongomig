import os
import uuid

import pytest
from pymongo import MongoClient

TEST_URI = os.environ.get("TEST_MONGODB_URI", "mongodb://localhost:27017/?directConnection=true")


@pytest.fixture
def db(monkeypatch):
    """A throwaway database per test, dropped afterwards."""
    client = MongoClient(TEST_URI)
    name = f"test_{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("MONGODB_URI", TEST_URI)
    monkeypatch.setenv("MONGODB_DATABASE", name)   # mongomig.yaml reads both
    yield client[name]
    client.drop_database(name)
