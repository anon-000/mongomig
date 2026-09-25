"""Distributed lock so only one runner migrates a database at a time.

A single document in ``__mongomig_lock``. Acquisition is one atomic ``find_one_and_update``
with ``upsert``: if another runner holds an unexpired lock the filter doesn't match, the
upsert collides on ``_id`` and we get ``DuplicateKeyError``. Expiry is computed with the
server clock (``$$NOW``) so clock skew between runners doesn't matter.

While held, a heartbeat thread keeps extending ``expires_at``. If the heartbeat ever finds the
lock gone (e.g. the process was paused past the TTL and someone else took over), ``lost`` is
set and ``check()`` raises, so the executor stops before doing more work.
"""

from __future__ import annotations

import os
import socket
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from mongomig._version import __version__
from mongomig.errors import LockError

if TYPE_CHECKING:
    from pymongo.collection import Collection
    from pymongo.database import Database

LOCK_DOC_ID = "migration"


class MigrationLock:
    def __init__(
        self,
        db: Database[dict[str, Any]],
        collection_name: str,
        *,
        ttl_seconds: int = 300,
        owner: str | None = None,
    ) -> None:
        self.db = db
        self.collection_name = collection_name
        self.ttl_ms = ttl_seconds * 1000
        self.token = uuid.uuid4().hex
        self.owner = owner or f"{socket.gethostname()}:{os.getpid()}"
        self.lost = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def collection(self) -> Collection[dict[str, Any]]:
        return self.db[self.collection_name]

    # --- primitive operations ------------------------------------------------------------

    def try_acquire(self) -> bool:
        from pymongo.errors import DuplicateKeyError

        mine = {"$eq": ["$lock_token", self.token]}
        claim = [
            {
                "$set": {
                    "lock_token": self.token,
                    "owner": self.owner,
                    "acquired_at": {"$cond": [mine, "$acquired_at", "$$NOW"]},
                    "expires_at": {"$add": ["$$NOW", self.ttl_ms]},
                    "mongomig_version": __version__,
                }
            }
        ]
        # 1) Our own lock, or an expired one: take it over. ($expr can't be used in an
        #    upsert filter, hence two steps.)
        taken = self.collection.update_one(
            {
                "_id": LOCK_DOC_ID,
                "$or": [
                    {"lock_token": self.token},
                    {"$expr": {"$lt": ["$expires_at", "$$NOW"]}},
                ],
            },
            claim,
        )
        if taken.matched_count:
            return True
        # 2) No lock document at all: create it. If someone else's lock exists, the filter
        #    doesn't match, the upsert tries to insert the same _id and fails.
        try:
            self.collection.update_one(
                {"_id": LOCK_DOC_ID, "lock_token": self.token}, claim, upsert=True
            )
        except DuplicateKeyError:
            return False
        return True

    def refresh(self) -> bool:
        result = self.collection.update_one(
            {"_id": LOCK_DOC_ID, "lock_token": self.token},
            [{"$set": {"expires_at": {"$add": ["$$NOW", self.ttl_ms]}}}],
        )
        return result.matched_count == 1

    def release(self) -> None:
        self.collection.delete_one({"_id": LOCK_DOC_ID, "lock_token": self.token})

    def holder(self) -> dict[str, Any] | None:
        return self.collection.find_one({"_id": LOCK_DOC_ID})

    # --- high level ----------------------------------------------------------------------

    def acquire(self, timeout: float = 0, poll_interval: float = 1.0) -> None:
        """Acquire or raise ``LockError``. ``timeout`` > 0 waits for the current holder."""
        deadline = time.monotonic() + timeout
        while True:
            if self.try_acquire():
                return
            if time.monotonic() >= deadline:
                break
            time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))

        holder = self.holder() or {}
        raise LockError(
            "Another MongoMig run holds the migration lock "
            f"(owner {holder.get('owner', '?')}, since {holder.get('acquired_at', '?')}).",
            suggestion=(
                "Wait for it to finish, or retry with --lock-timeout SECONDS. If that run "
                f"crashed, the lock expires automatically at {holder.get('expires_at', '?')}."
            ),
            details={k: holder.get(k) for k in ("owner", "acquired_at", "expires_at")},
        )

    def check(self) -> None:
        if self.lost:
            raise LockError(
                "The migration lock was lost while running (heartbeat could not renew it).",
                suggestion="Check `mongomig current`; re-run once no other runner is active.",
            )

    @contextmanager
    def hold(self, timeout: float = 0) -> Iterator[MigrationLock]:
        self.acquire(timeout)
        self._start_heartbeat()
        try:
            yield self
        finally:
            self._stop.set()
            if self._thread is not None:
                self._thread.join(timeout=5)
            if not self.lost:
                self.release()

    def _start_heartbeat(self) -> None:
        interval = max(self.ttl_ms / 1000 / 3, 0.5)

        def beat() -> None:
            while not self._stop.wait(interval):
                try:
                    if not self.refresh():
                        self.lost = True
                        return
                except Exception:  # transient network error: try again next beat
                    continue

        self._stop.clear()
        self._thread = threading.Thread(target=beat, name="mongomig-lock-heartbeat", daemon=True)
        self._thread.start()
