"""Read collection name, indexes and settings straight from Beanie ``Document`` classes.

Works without ``init_beanie()``: only the class definitions are inspected.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from mongomig.schema.indexes import build_index, normalize_index_keys

if TYPE_CHECKING:
    from pydantic import BaseModel

    from mongomig.metadata.registry import CollectionDef
    from mongomig.schema.models import IndexSchema


def is_beanie_document(model: type) -> bool:
    return any(
        c.__name__ == "Document" and c.__module__.startswith("beanie") for c in model.__mro__
    )


def collection_def_from_beanie(document: type[BaseModel]) -> CollectionDef:
    from mongomig.metadata.registry import CollectionDef

    if not is_beanie_document(document):
        raise TypeError(f"{document!r} is not a beanie.Document subclass")
    settings = getattr(document, "Settings", None)
    name = getattr(settings, "name", None) or document.__name__
    indexes = tuple(settings_indexes(getattr(settings, "indexes", None) or []))
    return CollectionDef(name=name, model=document, indexes=indexes, source="beanie")


def beanie_keep_nulls(document: type) -> bool:
    settings = getattr(document, "Settings", None)
    return bool(getattr(settings, "keep_nulls", True))


def beanie_uses_revision(document: type) -> bool:
    settings = getattr(document, "Settings", None)
    return bool(getattr(settings, "use_revision", False))


def settings_indexes(specs: list[Any]) -> list[IndexSchema]:
    """``Settings.indexes`` entries: ``"field"``, ``[("a", 1), ...]``, or ``IndexModel``."""
    result: list[IndexSchema] = []
    for spec in specs:
        document = getattr(spec, "document", None)  # pymongo.IndexModel
        if isinstance(document, Mapping):
            options = {k: v for k, v in document.items() if k not in ("key", "name")}
            unique = bool(options.pop("unique", False))
            sparse = bool(options.pop("sparse", False))
            result.append(
                build_index(
                    list(document["key"].items()),
                    name=document.get("name"),
                    unique=unique,
                    sparse=sparse,
                    **options,
                )
            )
        else:
            result.append(build_index(normalize_index_keys(spec)))
    return result


def indexed_field_index(path: str, index_type: Any, kwargs: dict[str, Any]) -> IndexSchema:
    """``Indexed(str, unique=True)`` on field ``email`` → index ``email_1``."""
    options = dict(kwargs)
    return build_index(
        [(path, index_type)],
        name=options.pop("name", None),
        unique=bool(options.pop("unique", False)),
        sparse=bool(options.pop("sparse", False)),
        **options,
    )
