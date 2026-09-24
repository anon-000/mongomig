"""MongoMig — Alembic-style schema evolution and migrations for MongoDB."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mongomig._version import __version__

if TYPE_CHECKING:
    from mongomig.api import aupgrade_to_head, downgrade, upgrade, upgrade_to_head
    from mongomig.errors import MongoMigError
    from mongomig.migrations.context import MigrationContext
    from mongomig.migrations.reporting import LoggingReporter, Reporter

__all__ = [
    "LoggingReporter",
    "MigrationContext",
    "MongoMigError",
    "Reporter",
    "__version__",
    "aupgrade_to_head",
    "downgrade",
    "upgrade",
    "upgrade_to_head",
]

# Public names are resolved lazily so `import mongomig` (and the CLI) stays fast.
_LAZY: dict[str, str] = {
    "MongoMigError": "mongomig.errors",
    "MigrationContext": "mongomig.migrations.context",
    "Reporter": "mongomig.migrations.reporting",
    "LoggingReporter": "mongomig.migrations.reporting",
    "upgrade": "mongomig.api",
    "downgrade": "mongomig.api",
    "upgrade_to_head": "mongomig.api",
    "aupgrade_to_head": "mongomig.api",
}


def __getattr__(name: str) -> Any:
    module_path = _LAZY.get(name)
    if module_path is None:
        raise AttributeError(f"module 'mongomig' has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module_path), name)
