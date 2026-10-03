"""The ``__mongomig_migrations`` collection: which revisions have been applied."""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from mongomig._version import __version__
from mongomig.migrations.graph import RevisionGraph
from mongomig.migrations.script import Script

if TYPE_CHECKING:
    from pymongo.collection import Collection
    from pymongo.database import Database

# "running" left behind means the process died mid-migration (the lock prevents a live one).
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
    error: str | None = None

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
            error=doc.get("error"),
        )


@dataclass(frozen=True)
class CurrentState:
    """Where the database is relative to the revision files."""

    applied: frozenset[str]
    applied_heads: list[str]
    pending: list[str]
    unknown: list[str]  # applied in the DB but no revision file (DB ahead of this code)
    failed: list[str]  # failed or interrupted ("running" with no live runner)
    modified: list[str]  # applied, but the file's checksum changed since
    # Squashes whose replaced revisions are all applied but which aren't recorded yet; the
    # next upgrade records them ("adopts") without running anything.
    adoptable: list[str] = field(default_factory=list)
    # Squashes with only some replaced revisions applied → the missing ones (run from the
    # squash archive on the next upgrade).
    partial: dict[str, list[str]] = field(default_factory=dict)


def run_metadata(environment: str | None, project_root: Path | None) -> dict[str, Any]:
    """Who/where/what ran a migration. Stored with each tracking record."""
    return {
        "environment": environment,
        "hostname": socket.gethostname(),
        "user": os.environ.get("USER") or os.environ.get("USERNAME"),
        "git_commit": _git_commit(project_root),
    }


def _git_commit(project_root: Path | None) -> str | None:
    for var in ("MONGOMIG_GIT_COMMIT", "GITHUB_SHA", "CI_COMMIT_SHA", "GIT_COMMIT"):
        if os.environ.get(var):
            return os.environ[var]
    if project_root is None:
        return None
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
    return None


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

    def record_running(self, script: Script, *, meta: dict[str, Any] | None = None) -> None:
        self._write(self._doc(script, "running", None, meta))

    def record_applied(
        self, script: Script, *, execution_time_ms: int | None, meta: dict[str, Any] | None = None
    ) -> None:
        self._write(self._doc(script, "applied", execution_time_ms, meta))

    def record_failed(
        self, script: Script, *, error: str, meta: dict[str, Any] | None = None
    ) -> None:
        doc = self._doc(script, "failed", None, meta)
        doc["error"] = error
        self._write(doc)

    def record_adopted(self, script: Script, *, meta: dict[str, Any] | None = None) -> None:
        """A squash whose replaced revisions all ran: mark it applied without running it."""
        doc = self._doc(script, "applied", None, meta)
        doc["adopted"] = True
        self._write(doc)

    def remove_many(self, revisions: Iterable[str]) -> None:
        self.collection.delete_many({"_id": {"$in": list(revisions)}})

    def remove(self, revision: str) -> None:
        self.collection.delete_one({"_id": revision})

    def stamp(self, scripts: Iterable[Script], *, meta: dict[str, Any] | None = None) -> None:
        """Make the applied set exactly ``scripts`` without running anything."""
        docs = []
        for script in scripts:
            doc = self._doc(script, "applied", None, meta)
            doc["stamped"] = True
            docs.append(doc)
        self.collection.delete_many({})
        if docs:
            self.collection.insert_many(docs)

    def _write(self, doc: dict[str, Any]) -> None:
        self.collection.replace_one({"_id": doc["_id"]}, doc, upsert=True)

    def _doc(
        self,
        script: Script,
        status: Status,
        execution_time_ms: int | None,
        meta: dict[str, Any] | None,
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
            "meta": meta or {},
        }


def compute_state(graph: RevisionGraph, records: list[AppliedRecord]) -> CurrentState:
    recorded = {r.revision for r in records if r.status == "applied"}
    applied, adoptable, partial = _with_squashes(graph, recorded)
    replaced = graph.replaced_ids()
    failed = [r.revision for r in records if r.status in ("failed", "running")]
    order = graph.topological_order()
    heads = [rev for rev in order if rev in applied and not graph.children[rev] & applied]
    pending = [rev for rev in order if rev not in applied]
    unknown = sorted(recorded - set(graph.scripts) - replaced)
    modified = [
        r.revision
        for r in records
        if r.status == "applied"
        and r.revision in graph
        and r.checksum is not None
        and r.checksum != graph.scripts[r.revision].checksum
    ]
    return CurrentState(
        applied=frozenset(applied),
        applied_heads=heads,
        pending=pending,
        unknown=unknown,
        failed=failed,
        modified=modified,
        adoptable=adoptable,
        partial=partial,
    )


def _with_squashes(
    graph: RevisionGraph, recorded: set[str]
) -> tuple[set[str], list[str], dict[str, list[str]]]:
    """Applied set where a squash counts as applied once everything it replaced is.

    Nested squashes (archived squash revisions) are resolved to a fixed point.
    """
    applied = set(recorded)
    squashes = [s for s in graph.scripts.values() if s.is_squash]
    squashes += [s for group in graph.archived.values() for s in group if s.is_squash]
    changed = True
    while changed:
        changed = False
        for squash in squashes:
            if squash.revision not in applied and all(r in applied for r in squash.replaces):
                applied.add(squash.revision)
                changed = True
    adoptable = [
        rev
        for rev in graph.topological_order()
        if graph.scripts[rev].is_squash and rev in applied and rev not in recorded
    ]
    partial: dict[str, list[str]] = {}
    for rev in graph.topological_order():
        script = graph.scripts[rev]
        if script.is_squash and rev not in applied:
            done = [r for r in script.replaces if r in applied]
            if done:
                partial[rev] = [r for r in script.replaces if r not in applied]
    return applied, adoptable, partial
