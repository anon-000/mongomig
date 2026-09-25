"""Pydantic model → ``FieldSchema``: which BSON types will actually be stored.

The answer depends on how the app writes documents (the storage profile), e.g. ``datetime`` is
a BSON ``date`` with ``model_dump()`` but a ``string`` with ``model_dump(mode="json")``.
Anything PyMongo cannot store (``date``, ``Decimal``, ``Enum`` members, ``UUID`` without a
uuidRepresentation) produces a warning, because inserts would fail at runtime.
"""

from __future__ import annotations

import datetime
import decimal
import enum
import types
import uuid
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Any, Literal, Union, get_args, get_origin

from mongomig.schema.models import FieldSchema

if TYPE_CHECKING:
    from pydantic import BaseModel
    from pydantic.fields import FieldInfo

    from mongomig.metadata.registry import StorageProfile

INT_FAMILY = ("int", "long")
MAX_DEPTH = 12


@dataclass
class MappingContext:
    profile: StorageProfile
    path: str = ""
    warnings: list[str] = field(default_factory=list)
    stack: tuple[type, ...] = ()  # models being expanded, to stop recursion
    use_enum_values: bool = False
    # Beanie `Indexed(...)` found on fields: (field path, index_type, kwargs)
    indexed_fields: list[tuple[str, Any, dict[str, Any]]] = field(default_factory=list)

    def warn(self, message: str) -> None:
        text = f"{self.path}: {message}" if self.path else message
        if text not in self.warnings:
            self.warnings.append(text)

    def child(self, name: str) -> MappingContext:
        return MappingContext(
            profile=self.profile,
            path=f"{self.path}.{name}" if self.path else name,
            warnings=self.warnings,
            stack=self.stack,
            use_enum_values=self.use_enum_values,
            indexed_fields=self.indexed_fields,
        )


def model_fields_schema(model: type[BaseModel], ctx: MappingContext) -> dict[str, FieldSchema]:
    """Map every field of ``model`` to the key/shape it has in stored documents."""
    config = getattr(model, "model_config", {}) or {}
    ctx = MappingContext(
        profile=ctx.profile,
        path=ctx.path,
        warnings=ctx.warnings,
        stack=(*ctx.stack, model),
        use_enum_values=bool(config.get("use_enum_values", False)) or ctx.use_enum_values,
        indexed_fields=ctx.indexed_fields,
    )
    result: dict[str, FieldSchema] = {}
    for attr, info in model.model_fields.items():
        if info.exclude:
            continue
        key = stored_key(attr, info, ctx.profile)
        result[key] = field_schema(info, ctx.child(key))
    return result


def stored_key(attr: str, info: FieldInfo, profile: StorageProfile) -> str:
    if profile.by_alias or profile.mode == "beanie":
        alias = info.serialization_alias or info.alias
        if alias:
            return alias
    return attr


def field_schema(info: FieldInfo, ctx: MappingContext) -> FieldSchema:
    schema = annotation_schema(info.annotation, ctx, metadata=list(info.metadata))
    has_default = not info.is_required()
    schema.has_default = has_default
    if has_default:
        schema.default_is_static, schema.default = _static_default(info)

    profile = ctx.profile
    if profile.exclude_unset:
        schema.required = info.is_required()
    elif profile.exclude_none and schema.nullable:
        schema.required = False
    else:
        schema.required = True  # model_dump() writes every field
    return schema


def annotation_schema(  # noqa: PLR0911 (type dispatch)
    annotation: Any, ctx: MappingContext, *, metadata: list[Any] | None = None
) -> FieldSchema:
    """Map a type annotation (recursively) to a ``FieldSchema``. ``required`` is left False."""
    for item in metadata or ():
        _record_beanie_index(item, ctx)

    origin = get_origin(annotation)

    if origin is Annotated:
        base, *extra = get_args(annotation)
        return annotation_schema(base, ctx, metadata=extra)

    if annotation is None or annotation is type(None):
        return FieldSchema(nullable=True)

    if origin in (Union, types.UnionType):
        return _union(get_args(annotation), ctx)

    if origin is Literal:
        return _enum_like(list(get_args(annotation)), ctx)

    if annotation is Any or annotation is object:
        return FieldSchema()

    if origin is not None:
        return _generic(annotation, origin, ctx)

    if isinstance(annotation, type):
        return _class(annotation, ctx)

    ctx.warn(f"unsupported annotation {annotation!r}; type not checked")
    return FieldSchema()


# --- helpers -----------------------------------------------------------------------------


def _union(members: tuple[Any, ...], ctx: MappingContext) -> FieldSchema:
    parts = [annotation_schema(m, ctx) for m in members]
    merged = FieldSchema(nullable=any(p.nullable for p in parts))
    if any(not p.bson_types and not p.nullable for p in parts):  # a member is Any
        return merged
    bson: list[str] = []
    for p in parts:
        bson.extend(t for t in p.bson_types if t not in bson)
    merged.bson_types = tuple(bson)
    objects = [p for p in parts if p.fields is not None]
    if len(objects) == 1:
        merged.fields = objects[0].fields
    elif len(objects) > 1:  # union of models: keep the shared shape loose
        merged.open = True
    arrays = [p for p in parts if p.items is not None]
    if arrays:
        merged.items = arrays[0] if len(arrays) == 1 else FieldSchema()
    enums = [p.enum for p in parts if p.enum is not None]
    if enums and len(enums) == len([p for p in parts if p.bson_types]):
        merged.enum = tuple(v for e in enums for v in e)
    return merged


def _enum_like(values: list[Any], ctx: MappingContext) -> FieldSchema:
    nullable = any(v is None for v in values)
    values = [v for v in values if v is not None]
    stored: list[Any] = []
    bson: list[str] = []
    for raw in values:
        v = raw.value if isinstance(raw, enum.Enum) else raw
        stored.append(v)
        for t in _python_value_types(v):
            if t not in bson:
                bson.append(t)
    return FieldSchema(bson_types=tuple(bson), nullable=nullable, enum=tuple(stored))


def _python_value_types(v: Any) -> tuple[str, ...]:
    if isinstance(v, bool):
        return ("bool",)
    if isinstance(v, int):
        return INT_FAMILY
    if isinstance(v, float):
        return ("double",)
    return ("string",)


def _generic(annotation: Any, origin: Any, ctx: MappingContext) -> FieldSchema:
    args = get_args(annotation)
    if isinstance(origin, type) and issubclass(origin, Mapping):
        # dict[str, X]: arbitrary keys, children not tracked
        return FieldSchema(bson_types=("object",), fields=None, open=True)
    if (
        isinstance(origin, type)
        and issubclass(origin, (Sequence, Set))
        and origin
        not in (
            str,
            bytes,
        )
    ):
        if origin is tuple and len(args) == 2 and args[1] is Ellipsis:
            items = annotation_schema(args[0], ctx.child("[]"))
        elif origin is tuple and args:
            items = _union(tuple(args), ctx.child("[]"))
        elif args:
            items = annotation_schema(args[0], ctx.child("[]"))
        else:
            items = FieldSchema()
        if origin in (set, frozenset) and ctx.profile.mode == "python":
            ctx.warn("sets cannot be encoded by PyMongo; convert to a list before inserting")
        return FieldSchema(bson_types=("array",), items=items)
    if origin is type:
        ctx.warn("type[...] fields cannot be stored; type not checked")
        return FieldSchema()
    ctx.warn(f"unsupported generic {annotation!r}; type not checked")
    return FieldSchema()


def _class(tp: type, ctx: MappingContext) -> FieldSchema:  # noqa: PLR0911, PLR0912
    from pydantic import BaseModel

    if hasattr(tp, "_indexed"):  # Beanie Indexed(str, unique=True) returns a str subclass
        _record_beanie_index(tp, ctx)
        tp = next(b for b in tp.__mro__[1:] if not hasattr(b, "_indexed"))

    override = _override(tp, ctx)
    if override is not None:
        return FieldSchema(bson_types=override)

    mode = ctx.profile.mode

    if issubclass(tp, BaseModel):
        if tp in ctx.stack or len(ctx.stack) > MAX_DEPTH:
            return FieldSchema(bson_types=("object",), open=True)  # recursive model
        return FieldSchema(bson_types=("object",), fields=model_fields_schema(tp, ctx))

    if issubclass(tp, enum.Enum):
        if mode == "python" and not ctx.use_enum_values:
            ctx.warn(
                f"{tp.__name__} enum members cannot be encoded by PyMongo; set "
                "model_config = ConfigDict(use_enum_values=True) or use storage='json'"
            )
        return _enum_like(list(tp), ctx)

    if issubclass(tp, bool):
        return FieldSchema(bson_types=("bool",))
    if issubclass(tp, int):
        return FieldSchema(bson_types=INT_FAMILY)
    if issubclass(tp, float):
        return FieldSchema(bson_types=("double",))
    if issubclass(tp, str):
        return FieldSchema(bson_types=("string",))

    if _is_object_id(tp):
        return FieldSchema(bson_types=("string",) if mode == "json" else ("objectId",))

    if issubclass(tp, datetime.datetime):
        return FieldSchema(bson_types=("string",) if mode == "json" else ("date",))
    if issubclass(tp, datetime.date):
        if mode == "python":
            ctx.warn("datetime.date cannot be encoded by PyMongo; use datetime instead")
        return FieldSchema(bson_types=("string",) if mode == "json" else ("date",))
    if issubclass(tp, datetime.time | datetime.timedelta):
        if mode != "json":
            ctx.warn(f"{tp.__name__} cannot be encoded as BSON; store it as a string or number")
        return FieldSchema(bson_types=("string",))

    if issubclass(tp, decimal.Decimal):
        if mode == "python":
            ctx.warn("Decimal needs bson.Decimal128 or a codec to be stored by PyMongo")
        return FieldSchema(bson_types=("string",) if mode == "json" else ("decimal",))
    if issubclass(tp, uuid.UUID):
        if mode == "python":
            ctx.warn("UUID needs uuidRepresentation='standard' on the MongoClient to be stored")
        return FieldSchema(bson_types=("string",) if mode == "json" else ("binData",))
    if issubclass(tp, bytes | bytearray):
        return FieldSchema(bson_types=("string",) if mode == "json" else ("binData",))

    if issubclass(tp, Mapping):
        return FieldSchema(bson_types=("object",), open=True)
    if issubclass(tp, list | tuple | set | frozenset):
        return FieldSchema(bson_types=("array",), items=FieldSchema())

    if _string_like(tp):
        if mode == "python" and tp.__name__ != "EmailStr":
            ctx.warn(
                f"{tp.__name__} values are objects after model_dump(); PyMongo can't store "
                "them (use str, or storage='json')"
            )
        return FieldSchema(bson_types=("string",))

    ctx.warn(f"unknown type {tp.__module__}.{tp.__qualname__}; type not checked")
    return FieldSchema()


def _override(tp: type, ctx: MappingContext) -> tuple[str, ...] | None:
    for cls in tp.__mro__:
        value = ctx.profile.type_overrides.get(cls)
        if value is not None:
            return (value,) if isinstance(value, str) else tuple(value)
    return None


def _is_object_id(tp: type) -> bool:
    try:
        from bson import ObjectId
    except ImportError:  # pragma: no cover - bson ships with pymongo
        return False
    return issubclass(tp, ObjectId) or any(c.__name__ == "PydanticObjectId" for c in tp.__mro__)


def _string_like(tp: type) -> bool:
    """Pydantic URL/DSN/e-mail types: strings in JSON, objects after a python-mode dump."""
    module = tp.__module__ or ""
    return module.startswith(("pydantic.networks", "pydantic_core"))


def _static_default(info: FieldInfo) -> tuple[bool, Any]:  # noqa: PLR0911
    from pydantic_core import PydanticUndefined

    if info.default_factory is not None:
        factory = info.default_factory
        if factory in (list, dict, tuple, set, frozenset):
            return True, [] if factory is not dict else {}
        return False, None
    value = info.default
    if value is PydanticUndefined:
        return False, None
    if isinstance(value, enum.Enum):
        value = value.value
    if value is None or isinstance(value, str | int | float | bool):
        return True, value
    if isinstance(value, list | tuple) and not value:
        return True, []
    if isinstance(value, dict) and not value:
        return True, {}
    return False, None


def _record_beanie_index(item: Any, ctx: MappingContext) -> None:
    indexed = getattr(item, "_indexed", None)
    if isinstance(indexed, tuple) and len(indexed) == 2:
        index_type, kwargs = indexed
        entry = (ctx.path, index_type, dict(kwargs))
        if entry not in ctx.indexed_fields:
            ctx.indexed_fields.append(entry)
