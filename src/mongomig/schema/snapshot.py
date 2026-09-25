"""``schema_snapshot.json``: the committed "last known expected schema".

``revision --autogenerate`` (M4) diffs the current models against this file and rewrites it;
it is deterministic (sorted keys, stable formatting) so changes review well in git.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mongomig.errors import ConfigError
from mongomig.schema.models import CollectionSchema

if TYPE_CHECKING:
    from mongomig.metadata.registry import StorageProfile

SNAPSHOT_FORMAT = 1


@dataclass
class Snapshot:
    collections: dict[str, CollectionSchema] = field(default_factory=dict)
    storage: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mongomig_format": SNAPSHOT_FORMAT,
            "storage": self.storage,
            "collections": {
                name: self.collections[name].to_dict() for name in sorted(self.collections)
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Snapshot:
        fmt = data.get("mongomig_format", SNAPSHOT_FORMAT)
        if fmt > SNAPSHOT_FORMAT:
            raise ConfigError(
                f"schema_snapshot.json has format {fmt}; this mongomig supports {SNAPSHOT_FORMAT}.",
                suggestion="Upgrade mongomig: pip install -U mongomig",
            )
        return cls(
            collections={
                name: CollectionSchema.from_dict(name, body, source="snapshot")
                for name, body in (data.get("collections") or {}).items()
            },
            storage=data.get("storage"),
        )

    @classmethod
    def from_schemas(
        cls, schemas: dict[str, CollectionSchema], profile: StorageProfile
    ) -> Snapshot:
        return cls(collections=dict(schemas), storage=profile.to_dict())


def empty_snapshot() -> dict[str, Any]:
    return Snapshot().to_dict()


def canonical_json(data: Any) -> str:
    """Deterministic serialisation so git diffs and hashes are stable."""
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n"


def content_hash(data: Any) -> str:
    compact = json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return "sha256:" + hashlib.sha256(compact.encode("utf-8")).hexdigest()


def snapshot_hash(path: Path) -> str | None:
    """Hash of the snapshot's *content* (not formatting); ``None`` if there is no snapshot."""
    if not path.is_file():
        return None
    return content_hash(json.loads(path.read_text(encoding="utf-8")))


def load_snapshot(path: Path) -> Snapshot:
    if not path.is_file():
        return Snapshot()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"{path.name} is not valid JSON (line {exc.lineno}): {exc.msg}",
            suggestion="Resolve merge conflicts in the snapshot file, or restore it from git.",
        ) from None
    return Snapshot.from_dict(data)


def write_snapshot(path: Path, snapshot: Snapshot | dict[str, Any]) -> None:
    data = snapshot.to_dict() if isinstance(snapshot, Snapshot) else snapshot
    path.write_text(canonical_json(data), encoding="utf-8")
