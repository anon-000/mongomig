"""MongoMig — Alembic-style schema evolution and migrations for MongoDB."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mongomig._version import __version__

if TYPE_CHECKING:
    from mongomig.errors import MongoMigError

__all__ = ["MongoMigError", "__version__"]

# Public names are resolved lazily so `import mongomig` (and the CLI) stays fast.
_LAZY: dict[str, str] = {
    "MongoMigError": "mongomig.errors",
}


def __getattr__(name: str) -> Any:
    module_path = _LAZY.get(name)
    if module_path is None:
        raise AttributeError(f"module 'mongomig' has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module_path), name)
