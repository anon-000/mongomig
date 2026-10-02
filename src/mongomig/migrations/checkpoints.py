"""Checkpoints: how far each batched loop of a migration got, so a re-run continues.

One document per loop in ``__mongomig_checkpoints``, keyed by revision, direction and the
loop's position in the migration. A checkpoint is only used if

- the migration file is unchanged since it was written (checksum), and
- the loop looks the same (fingerprint of collection, filter and update),

otherwise the loop starts over: resuming different code from a stale position is worse than
redoing work. Checkpoints are deleted when the migration succeeds.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pymongo.client_session import ClientSession
    from pymongo.collection import Collection
    from pymongo.database import Database


@dataclass
class Checkpoint:
    key: str
    collection: str
    fingerprint: str
    last_id: Any = None
    has_position: bool = False
    processed: int = 0
    matched: int = 0
    modified: int = 0
    batches: int = 0
    done: bool = False


def fingerprint(*parts: Any) -> str:
    raw = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class CheckpointStore:
    def __init__(
        self,
        db: Database[dict[str, Any]],
        collection_name: str,
        *,
        revision: str,
        direction: str,
        checksum: str,
    ) -> None:
        self.db = db
        self.collection_name = collection_name
        self.revision = revision
        self.direction = direction
        self.checksum = checksum

    @property
    def collection(self) -> Collection[dict[str, Any]]:
        return self.db[self.collection_name]

    def _id(self, key: str) -> dict[str, str]:
        return {"r": self.revision, "d": self.direction, "k": key}

    def discard_stale(self) -> int:
        """Drop this revision's checkpoints written by a different version of the file."""
        result = self.collection.delete_many(
            {"revision": self.revision, "checksum": {"$ne": self.checksum}}
        )
        return result.deleted_count

    def load(self, key: str, fp: str) -> Checkpoint | None:
        doc = self.collection.find_one({"_id": self._id(key)})
        if doc is None or doc.get("fingerprint") != fp or doc.get("checksum") != self.checksum:
            return None
        return Checkpoint(
            key=key,
            collection=doc["collection"],
            fingerprint=fp,
            last_id=doc.get("last_id"),
            has_position="last_id" in doc,
            processed=doc.get("processed", 0),
            matched=doc.get("matched", 0),
            modified=doc.get("modified", 0),
            batches=doc.get("batches", 0),
            done=doc.get("done", False),
        )

    def save(self, cp: Checkpoint, *, session: ClientSession | None = None) -> None:
        doc: dict[str, Any] = {
            "_id": self._id(cp.key),
            "revision": self.revision,
            "direction": self.direction,
            "key": cp.key,
            "collection": cp.collection,
            "fingerprint": cp.fingerprint,
            "checksum": self.checksum,
            "processed": cp.processed,
            "matched": cp.matched,
            "modified": cp.modified,
            "batches": cp.batches,
            "done": cp.done,
            "updated_at": datetime.now(UTC),
        }
        if cp.has_position:
            doc["last_id"] = cp.last_id
        self.collection.replace_one({"_id": doc["_id"]}, doc, upsert=True, session=session)

    def clear(self) -> None:
        """The migration finished: its checkpoints (both directions) are no longer needed."""
        self.collection.delete_many({"revision": self.revision})


def checkpoint_summary(
    db: Database[dict[str, Any]], collection_name: str, revisions: list[str]
) -> dict[str, list[dict[str, Any]]]:
    """Progress saved for these revisions, for ``mongomig current``."""
    result: dict[str, list[dict[str, Any]]] = {}
    if not revisions:
        return result
    cursor = db[collection_name].find(
        {"revision": {"$in": revisions}}, sort=[("revision", 1), ("key", 1)]
    )
    for doc in cursor:
        result.setdefault(doc["revision"], []).append(
            {
                "direction": doc.get("direction"),
                "collection": doc.get("collection"),
                "processed": doc.get("processed", 0),
                "done": doc.get("done", False),
                "updated_at": doc.get("updated_at"),
            }
        )
    return result
