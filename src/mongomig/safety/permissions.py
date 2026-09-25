"""Which MongoDB privileges MongoMig needs, and whether the connected user has them.

Minimum role for running migrations: ``readWrite`` on the database. Managing validators
(``collMod``) needs ``dbAdmin`` too, or a custom role with ``collMod``. Read-only commands
(``current``, ``inspect``, ``plan``) only need ``read``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

REQUIRED_ACTIONS = {
    "find": "read documents",
    "insert": "record migrations",
    "update": "backfills and the migration lock",
    "remove": "downgrades and the migration lock",
    "createCollection": "the tracking and lock collections",
    "createIndex": "create_index",
    "dropIndex": "drop_index",
    "listCollections": "inspect collections",
    "listIndexes": "inspect indexes",
}
OPTIONAL_ACTIONS = {
    "collMod": "set_validator / remove_validator (dbAdmin role)",
    "dropCollection": "drop_collection and `mongomig backups --drop`",
    "renameCollectionSameDB": "rename_collection and drop_collection(backup=True)",
}


def database_actions(privileges: Iterable[Mapping[str, Any]], database: str) -> set[str]:
    """Actions granted on every collection of ``database`` (collection-specific grants are
    ignored: MongoMig needs database-wide rights)."""
    actions: set[str] = set()
    for privilege in privileges:
        resource = privilege.get("resource", {})
        if resource.get("anyResource"):
            actions.update(privilege.get("actions", []))
            continue
        if resource.get("db") in (database, "") and resource.get("collection") == "":
            actions.update(privilege.get("actions", []))
    return actions


def missing_actions(actions: set[str]) -> tuple[list[str], list[str]]:
    """(missing required, missing optional) action names."""
    required = [a for a in REQUIRED_ACTIONS if a not in actions]
    optional = [a for a in OPTIONAL_ACTIONS if a not in actions]
    return required, optional
