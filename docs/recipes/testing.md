# Test your migrations

Two kinds of tests catch most problems before production:

1. **The whole history applies (and reverts) on an empty database**, which catches broken
   migrations.
2. **A migration's data logic is right**, tested on a few hand-made documents.

Both use a real MongoDB (the repository's `docker-compose.yml` starts one), and each test gets
a throwaway database.

```python
# tests/conftest.py
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
```

```python
# tests/test_migrations.py
import mongomig
from mongomig import MigrationContext


def test_every_migration_applies_and_reverts(db):
    """The whole history runs on an empty database, and back down to base."""
    mongomig.upgrade(yes=True)
    assert mongomig.current_state().pending == []
    mongomig.downgrade("base")
    assert mongomig.current_state().applied_heads == []


def test_split_customer_names(db):
    """One migration's data logic, on hand-made documents."""
    db.orders.insert_many([
        {"total": 10, "full_name": "Ada Lovelace"},
        {"total": 20, "full_name": "Plato"},
        {"total": 30, "customer": {"first": "Already", "last": "Migrated"}},
    ])
    migration = mongomig.load_revision("split_customer_names")
    migration.upgrade(MigrationContext(db))

    assert db.orders.find_one({"total": 10})["customer"] == {"first": "Ada", "last": "Lovelace"}
    assert db.orders.find_one({"total": 20})["customer"] == {"first": "Plato", "last": ""}
    assert db.orders.count_documents({"full_name": {"$exists": True}}) == 0
```

- **`mongomig.upgrade()` / `downgrade()`** use your `mongomig.yaml`; the fixture points
  `MONGODB_DATABASE` at the throwaway database.
- **`mongomig.load_revision(...)`** imports a revision file by id, id prefix, or part of its
  file name. Call its `upgrade(ctx)` with a `MigrationContext` on any database.

The app's startup and readiness check can be tested the same way (FastAPI's `TestClient`
needs `httpx`):

```python
# tests/test_app.py
from fastapi.testclient import TestClient


def test_startup_migrates_and_reports_ready(db):
    from app.main import app

    with TestClient(app) as client:          # runs the lifespan: migrations applied
        response = client.get("/health/ready")
        assert response.status_code == 200
        assert response.json() == {"ready": True, "pending": [], "failed": []}
```

```console
$ pytest -v tests
collecting ... collected 3 items
tests/test_app.py::test_startup_migrates_and_reports_ready PASSED        [ 33%]
tests/test_migrations.py::test_every_migration_applies_and_reverts PASSED [ 66%]
tests/test_migrations.py::test_split_customer_names PASSED               [100%]
============================== 3 passed in 0.87s ===============================
```

!!! tip "In CI"
    Run these tests with a MongoDB service (e.g. `docker compose up -d --wait`). Transactions
    (`transactional=True`, `ctx.transaction()`) need a replica set; the repository's
    `docker-compose.yml` provides a single-node one.
