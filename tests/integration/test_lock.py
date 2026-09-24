from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pymongo.database import Database

from mongomig.errors import ExitCode, LockError
from mongomig.migrations.lock import LOCK_DOC_ID, MigrationLock

pytestmark = pytest.mark.integration


def make(db: Database[dict[str, Any]], ttl: int = 30, owner: str = "test") -> MigrationLock:
    return MigrationLock(db, "__mongomig_lock", ttl_seconds=ttl, owner=owner)


def test_acquire_and_release(mongo_db: Database[dict[str, Any]]) -> None:
    a = make(mongo_db, owner="a")
    assert a.try_acquire()
    assert a.try_acquire()  # re-entrant for the same token
    holder = a.holder()
    assert holder is not None
    assert holder["owner"] == "a"
    assert holder["expires_at"] > datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=5)
    a.release()
    assert a.holder() is None


def test_second_runner_is_blocked(mongo_db: Database[dict[str, Any]]) -> None:
    a, b = make(mongo_db, owner="runner-a"), make(mongo_db, owner="runner-b")
    assert a.try_acquire()
    assert not b.try_acquire()
    with pytest.raises(LockError, match="runner-a") as exc:
        b.acquire(timeout=0)
    assert exc.value.exit_code == ExitCode.LOCK_FAILURE
    b.release()  # must not release someone else's lock
    assert a.holder() is not None


def test_expired_lock_is_taken_over(mongo_db: Database[dict[str, Any]]) -> None:
    mongo_db["__mongomig_lock"].insert_one(
        {
            "_id": LOCK_DOC_ID,
            "lock_token": "crashed",
            "owner": "dead-host",
            "expires_at": datetime.now(UTC) - timedelta(seconds=1),
        }
    )
    b = make(mongo_db, owner="b")
    assert b.try_acquire()
    assert b.holder()["owner"] == "b"  # type: ignore[index]


def test_waits_for_release(mongo_db: Database[dict[str, Any]]) -> None:
    a, b = make(mongo_db, owner="a"), make(mongo_db, owner="b")
    a.acquire()
    threading.Timer(0.5, a.release).start()
    start = time.monotonic()
    b.acquire(timeout=5, poll_interval=0.1)
    assert 0.3 < time.monotonic() - start < 4
    assert b.holder()["owner"] == "b"  # type: ignore[index]


def test_hold_heartbeat_keeps_lock_alive(mongo_db: Database[dict[str, Any]]) -> None:
    a, b = make(mongo_db, ttl=1, owner="a"), make(mongo_db, ttl=1, owner="b")
    with a.hold():
        time.sleep(2.2)  # > TTL: only the heartbeat keeps it
        assert not b.try_acquire()
        a.check()
    assert a.holder() is None  # released on exit


def test_lost_lock_is_detected(mongo_db: Database[dict[str, Any]]) -> None:
    a = make(mongo_db, ttl=1, owner="a")
    with a.hold():
        mongo_db["__mongomig_lock"].delete_many({})  # someone force-removed it
        time.sleep(1.2)
        with pytest.raises(LockError, match="lost"):
            a.check()
