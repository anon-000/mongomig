"""Index key specs: normalisation, default names, and server index documents → IndexSchema."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from mongomig.schema.models import IndexSchema

IndexKeys = str | Sequence[str] | Sequence[tuple[str, Any]] | Mapping[str, Any]

# Keys in a server index document that aren't user-facing options.
_INTERNAL_INDEX_KEYS = frozenset({"v", "key", "name", "ns", "unique", "sparse", "background"})


def normalize_index_keys(keys: IndexKeys) -> list[tuple[str, Any]]:
    """``"email"`` / ``["a", "b"]`` / ``[("a", 1), ("b", -1)]`` / ``{"a": 1}`` → key list."""
    if isinstance(keys, str):
        return [(keys, 1)]
    if isinstance(keys, Mapping):
        return list(keys.items())
    normalized: list[tuple[str, Any]] = []
    for item in keys:
        if isinstance(item, str):
            normalized.append((item, 1))
        elif isinstance(item, tuple | list) and len(item) == 2 and isinstance(item[0], str):
            normalized.append((item[0], item[1]))
        else:
            raise TypeError(f"Invalid index key specification: {item!r}")
    if not normalized:
        raise ValueError("An index needs at least one key.")
    return normalized


def default_index_name(keys: Sequence[tuple[str, Any]]) -> str:
    """The name MongoDB/PyMongo would generate: ``email_1``, ``a_1_b_-1``, ``body_text``."""
    return "_".join(f"{k}_{v}" for k, v in keys)


def canonical(value: Any) -> Any:
    """Plain, comparable Python values (SON → dict, Int64/float ints → int)."""
    if isinstance(value, Mapping):
        return {str(k): canonical(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [canonical(v) for v in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, int):
        return int(value)
    return value


def index_from_server(doc: Mapping[str, Any]) -> IndexSchema:
    """Convert a ``list_indexes()`` document."""
    keys = tuple((str(k), canonical(v)) for k, v in doc["key"].items())
    options = {k: canonical(v) for k, v in doc.items() if k not in _INTERNAL_INDEX_KEYS}
    return IndexSchema(
        name=str(doc["name"]),
        keys=keys,
        unique=bool(doc.get("unique", False)),
        sparse=bool(doc.get("sparse", False)),
        options=tuple(sorted(options.items())),
    )


def build_index(
    keys: IndexKeys,
    *,
    name: str | None = None,
    unique: bool = False,
    sparse: bool = False,
    **options: Any,
) -> IndexSchema:
    key_list = [(k, canonical(v)) for k, v in normalize_index_keys(keys)]
    return IndexSchema(
        name=name or default_index_name(key_list),
        keys=tuple(key_list),
        unique=unique,
        sparse=sparse,
        options=tuple(sorted((k, canonical(v)) for k, v in options.items())),
    )
