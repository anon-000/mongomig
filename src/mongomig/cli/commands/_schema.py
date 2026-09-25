"""Shared by `diff`, `revision --autogenerate` and `baseline`: models vs snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from mongomig.errors import ConfigError

if TYPE_CHECKING:
    from mongomig.config.models import LoadedConfig
    from mongomig.metadata.registry import MongoMetadata
    from mongomig.migrations.graph import RevisionGraph
    from mongomig.schema.diff import DiffResult
    from mongomig.schema.models import CollectionSchema
    from mongomig.schema.snapshot import Snapshot


@dataclass
class SchemaState:
    metadata: MongoMetadata
    declared: dict[str, CollectionSchema]
    snapshot: Snapshot
    new_snapshot: Snapshot
    diff: DiffResult


def require_metadata(config: LoadedConfig) -> MongoMetadata:
    from mongomig.config.envpy import load_metadata

    metadata = load_metadata(config)
    if metadata is None:
        raise ConfigError(
            "No models registered: migrations/env.py sets target_metadata = None.",
            suggestion="Register your models in env.py (see its comments), then check them "
            "with `mongomig models`.",
        )
    return metadata


def compute(config: LoadedConfig, graph: RevisionGraph, renames: list[str]) -> SchemaState:
    from mongomig.schema.diff import diff_schemas, parse_renames
    from mongomig.schema.snapshot import Snapshot, load_snapshot

    metadata = require_metadata(config)
    declared, type_warnings = metadata.schemas()
    snapshot = load_snapshot(config.snapshot_path)
    diff = diff_schemas(snapshot.collections, declared, renames=parse_renames(renames))
    diff.warnings = (
        type_warnings + diff.warnings + consistency_warnings(config, graph, snapshot, metadata)
    )
    return SchemaState(
        metadata=metadata,
        declared=declared,
        snapshot=snapshot,
        new_snapshot=Snapshot.from_schemas(declared, metadata.profile),
        diff=diff,
    )


def consistency_warnings(
    config: LoadedConfig, graph: RevisionGraph, snapshot: Snapshot, metadata: MongoMetadata
) -> list[str]:
    from mongomig.schema.snapshot import snapshot_hash

    warnings: list[str] = []
    old_mode = (snapshot.storage or {}).get("mode")
    if snapshot.collections and old_mode and old_mode != metadata.profile.mode:
        warnings.append(
            f"storage profile changed from {old_mode!r} to {metadata.profile.mode!r}: stored "
            "types (dates, UUIDs, ...) differ between them, so many fields may show as changed"
        )
    heads = graph.heads()
    if len(heads) == 1:
        recorded = graph.scripts[heads[0]].snapshot_hash
        current = snapshot_hash(config.snapshot_path)
        if recorded is not None and current is not None and recorded != current:
            warnings.append(
                f"schema_snapshot.json changed since head revision {heads[0]} was created "
                "(hand edit or merge?); the diff is relative to the file as it is now"
            )
    return warnings
