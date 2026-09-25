from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pymongo.database import Database

from mongomig.migrations.tracker import MigrationTracker
from tests.helpers import make_revision

pytestmark = pytest.mark.integration


def test_ensure_is_idempotent(mongo_db: Database[dict[str, Any]]) -> None:
    tracker = MigrationTracker(mongo_db, "__mongomig_migrations")
    assert not tracker.exists()
    tracker.ensure()
    tracker.ensure()
    assert tracker.exists()
    index_names = set(tracker.collection.index_information())
    assert {"status_1", "applied_at_1"} <= index_names


def test_records_on_missing_collection_is_read_only(mongo_db: Database[dict[str, Any]]) -> None:
    tracker = MigrationTracker(mongo_db, "__mongomig_migrations")
    assert tracker.records() == []
    assert not tracker.exists()


def test_record_lifecycle(mongo_db: Database[dict[str, Any]], versions_dir: Path) -> None:
    tracker = MigrationTracker(mongo_db, "__mongomig_migrations")
    tracker.ensure()
    s1 = make_revision(versions_dir, "rev000000001", minute=0)
    s2 = make_revision(versions_dir, "rev000000002", "rev000000001", minute=1)

    tracker.record_applied(s1, execution_time_ms=12, meta={"environment": "test"})
    tracker.record_failed(s2, error="boom")
    records = {r.revision: r for r in tracker.records()}
    assert records["rev000000001"].status == "applied"
    assert records["rev000000001"].checksum == s1.checksum
    assert records["rev000000001"].execution_time_ms == 12
    assert records["rev000000002"].status == "failed"
    assert records["rev000000002"].down_revisions == ("rev000000001",)
    assert tracker.applied_ids() == {"rev000000001"}

    doc = tracker.collection.find_one({"_id": "rev000000001"})
    assert doc is not None
    assert doc["meta"]["environment"] == "test"
    assert doc["mongomig_version"]

    tracker.record_applied(s2, execution_time_ms=5)  # retry succeeds -> replaces failed record
    assert tracker.applied_ids() == {"rev000000001", "rev000000002"}

    tracker.remove("rev000000002")
    assert tracker.applied_ids() == {"rev000000001"}
