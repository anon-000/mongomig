"""The ``__mongomig_migrations`` collection: which revisions have been applied."""

from __future__ import annotations

import contextlib
import os
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from mongomig._version import __version__
from mongomig.migrations.graph import RevisionGraph
from mongomig.migrations.script import Script

if TYPE_CHECKING:
    from pymongo.collection import Collection
    from pymongo.database import Database

Status = Literal["applied", "failed", "running"]


@dataclass(frozen=True)
class AppliedRecord:
    revision: str
    status: Status
    down_revisions: tuple[str, ...]
    description: str
    checksum: str | None
    applied_at: datetime | None
    execution_time_ms: int | None

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> AppliedRecord:
        down = doc.get("down_revision")
        if down is None:
            downs: tuple[str, ...] = ()
        elif isinstance(down, str):
            downs = (down,)
        else:
            downs = tuple(down)
        return cls(
            revision=str(doc["_id"]),
            status=doc.get("status", "applied"),
            down_revisions=downs,
            description=doc.get("description", ""),
            checksum=doc.get("checksum"),
            applied_at=doc.get("applied_at"),
            execution_time_ms=doc.get("execution_time_ms"),
        )


@dataclass(frozen=True)
class CurrentState:
    """Where the database is relative to the revision files."""

    applied_heads: list[str]
    pending: list[str]
    unknown: list[str]  # applied in the DB but no revision file (DB ahead of this code)
    failed: list[str]


class MigrationTracker:
    def __init__(self, db: Database[dict[str, Any]], collection_name: str) -> None:
        self.db = db
        self.collection_name = collection_name

    @property
    def collection(self) -> Collection[dict[str, Any]]:
        return self.db[self.collection_name]

    def exists(self) -> bool:
        return self.collection_name in self.db.list_collection_names(
            filter={"name": self.collection_name}
        )

    def ensure(self) -> None:
        """Create the tracking collection and its indexes (idempotent, race-safe)."""
        from pymongo.errors import CollectionInvalid

        if not self.exists():
            with contextlib.suppress(CollectionInvalid):  # another runner created it first
                self.db.create_collection(self.collection_name)
        self.collection.create_index("status", name="status_1")
        self.collection.create_index("applied_at", name="applied_at_1")

    def records(self) -> list[AppliedRecord]:
        """All records (any status); read-only, safe when the collection doesn't exist."""
        docs = self.collection.find({}, sort=[("applied_at", 1), ("_id", 1)])
        return [AppliedRecord.from_doc(doc) for doc in docs]

    def applied_ids(self) -> set[str]:
        return {r.revision for r in self.records() if r.status == "applied"}

    def record_applied(
        self, script: Script, *, execution_time_ms: int, environment: str | None = None
    ) -> None:
        self.collection.replace_one(
            {"_id": script.revision},
            self._doc(script, "applied", execution_time_ms, environment),
            upsert=True,
        )

    def record_failed(self, script: Script, *, error: str, environment: str | None = None) -> None:
        doc = self._doc(script, "failed", None, environment)
        doc["error"] = error
        self.collection.replace_one({"_id": script.revision}, doc, upsert=True)

    def remove(self, revision: str) -> None:
        self.collection.delete_one({"_id": revision})

    def _doc(
        self,
        script: Script,
        status: Status,
        execution_time_ms: int | None,
        environment: str | None,
    ) -> dict[str, Any]:
        downs = script.down_revisions
        return {
            "_id": script.revision,
            "revision": script.revision,
            "down_revision": None if not downs else downs[0] if len(downs) == 1 else list(downs),
            "description": script.message,
            "status": status,
            "checksum": script.checksum,
            "applied_at": datetime.now(UTC),
            "execution_time_ms": execution_time_ms,
            "mongomig_version": __version__,
            "meta": {
                "environment": environment,
                "hostname": socket.gethostname(),
                "user": os.environ.get("USER") or os.environ.get("USERNAME"),
            },
        }


def compute_state(graph: RevisionGraph, records: list[AppliedRecord]) -> CurrentState:
    applied = {r.revision for r in records if r.status == "applied"}
    failed = [r.revision for r in records if r.status == "failed"]
    known_applied = {rev for rev in applied if rev in graph}
    heads = [
        rev
        for rev in graph.topological_order()
        if rev in known_applied and not (graph.children[rev] & known_applied)
    ]
    pending = [rev for rev in graph.topological_order() if rev not in applied]
    unknown = sorted(applied - set(graph.scripts))
    return CurrentState(applied_heads=heads, pending=pending, unknown=unknown, failed=failed)
