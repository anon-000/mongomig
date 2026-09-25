"""The common schema representation every source is normalised into.

Pydantic models, Beanie documents, observed documents and (later) validators all become
``CollectionSchema`` objects, so the diff engine never sees source-specific details.

These are plain dataclasses with explicit ``to_dict``/``from_dict`` so the snapshot file format
is stable and reviewable in git.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# MongoDB $jsonSchema ``bsonType`` aliases.
BSON_TYPES = (
    "double",
    "string",
    "object",
    "array",
    "binData",
    "objectId",
    "bool",
    "date",
    "null",
    "regex",
    "javascript",
    "int",
    "timestamp",
    "long",
    "decimal",
    "minKey",
    "maxKey",
)
# int and long are one family: PyMongo picks int32/int64 by value, so seeing both is normal.
INTEGER_TYPES = frozenset({"int", "long"})

SchemaSource = Literal["declared", "observed", "snapshot"]


@dataclass(frozen=True)
class FieldStats:
    """Observed statistics (only on inferred schemas)."""

    count: int  # documents (or parent objects) where the field was present
    presence: float  # count / number of parents that could have had it
    types: dict[str, float]  # bson type -> share of present values (incl. "null")

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "presence": round(self.presence, 6),
            "types": {k: round(v, 6) for k, v in sorted(self.types.items())},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FieldStats:
        return cls(count=data["count"], presence=data["presence"], types=dict(data["types"]))


@dataclass
class FieldSchema:
    """One field. ``bson_types`` excludes ``null``; nullability is ``nullable``.

    An empty ``bson_types`` means "any type" (e.g. ``Any`` in a model).
    ``required`` means the field is present in every stored document (not the Pydantic notion).
    """

    bson_types: tuple[str, ...] = ()
    required: bool = False
    nullable: bool = False
    fields: dict[str, FieldSchema] | None = None  # sub-fields when the value is an object
    items: FieldSchema | None = None  # element schema when the value is an array
    enum: tuple[Any, ...] | None = None
    open: bool = False  # object with arbitrary keys (dict[str, X], maps): children not tracked
    has_default: bool = False
    default: Any = None  # only meaningful when has_default and the default is static
    default_is_static: bool = False
    stats: FieldStats | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "bson_types": list(self.bson_types),
            "required": self.required,
            "nullable": self.nullable,
        }
        if self.fields is not None:
            data["fields"] = {k: v.to_dict() for k, v in self.fields.items()}
        if self.items is not None:
            data["items"] = self.items.to_dict()
        if self.enum is not None:
            data["enum"] = list(self.enum)
        if self.open:
            data["open"] = True
        if self.has_default:
            data["default"] = (
                {"value": self.default} if self.default_is_static else {"dynamic": True}
            )
        if self.stats is not None:
            data["stats"] = self.stats.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FieldSchema:
        default = data.get("default")
        return cls(
            bson_types=tuple(data.get("bson_types", ())),
            required=data.get("required", False),
            nullable=data.get("nullable", False),
            fields=(
                {k: cls.from_dict(v) for k, v in data["fields"].items()}
                if "fields" in data
                else None
            ),
            items=cls.from_dict(data["items"]) if "items" in data else None,
            enum=tuple(data["enum"]) if "enum" in data else None,
            open=data.get("open", False),
            has_default=default is not None,
            default=default.get("value") if default else None,
            default_is_static=bool(default and "value" in default),
            stats=FieldStats.from_dict(data["stats"]) if "stats" in data else None,
        )

    def type_label(self) -> str:
        """Human-readable type, e.g. ``int | null`` or ``array<string>``."""
        parts: list[str] = []
        for t in self.bson_types:
            if t == "array" and self.items is not None and self.items.bson_types:
                parts.append(f"array<{self.items.type_label()}>")
            else:
                parts.append(t)
        label = " | ".join(_collapse_integers(parts)) or "any"
        return f"{label} | null" if self.nullable else label


@dataclass(frozen=True)
class IndexSchema:
    name: str
    keys: tuple[tuple[str, Any], ...]
    unique: bool = False
    sparse: bool = False
    # Everything else, canonicalised: partialFilterExpression, expireAfterSeconds, collation,
    # hidden, weights, default_language, 2dsphereIndexVersion, ...
    options: tuple[tuple[str, Any], ...] = ()

    @property
    def options_dict(self) -> dict[str, Any]:
        return dict(self.options)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"name": self.name, "keys": [list(k) for k in self.keys]}
        if self.unique:
            data["unique"] = True
        if self.sparse:
            data["sparse"] = True
        if self.options:
            data["options"] = dict(self.options)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IndexSchema:
        return cls(
            name=data["name"],
            keys=tuple((k, v) for k, v in data["keys"]),
            unique=data.get("unique", False),
            sparse=data.get("sparse", False),
            options=tuple(sorted(data.get("options", {}).items())),
        )

    def describe(self) -> str:
        arrows = {1: "↑", -1: "↓"}
        keys = ", ".join(f"{k} {arrows.get(v, v)}" for k, v in self.keys)
        flags = [f for f, on in (("unique", self.unique), ("sparse", self.sparse)) if on]
        flags += [f"{k}={v}" for k, v in self.options]
        return f"{self.name} ({keys}{', ' if flags else ''}{', '.join(flags)})"


@dataclass
class CollectionSchema:
    name: str
    fields: dict[str, FieldSchema] = field(default_factory=dict)
    indexes: list[IndexSchema] = field(default_factory=list)
    validator: dict[str, Any] | None = None
    validation_level: str | None = None
    validation_action: str | None = None
    source: SchemaSource = "declared"
    model: str | None = None  # "app.models.User" for declared schemas

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "fields": {k: v.to_dict() for k, v in self.fields.items()},
            "indexes": [ix.to_dict() for ix in sorted(self.indexes, key=lambda i: i.name)],
            "validator": self.validator,
        }
        if self.validator is not None:
            data["validation_level"] = self.validation_level
            data["validation_action"] = self.validation_action
        if self.model:
            data["model"] = self.model
        return data

    @classmethod
    def from_dict(
        cls, name: str, data: dict[str, Any], source: SchemaSource = "snapshot"
    ) -> CollectionSchema:
        return cls(
            name=name,
            fields={k: FieldSchema.from_dict(v) for k, v in data.get("fields", {}).items()},
            indexes=[IndexSchema.from_dict(ix) for ix in data.get("indexes", [])],
            validator=data.get("validator"),
            validation_level=data.get("validation_level"),
            validation_action=data.get("validation_action"),
            source=source,
            model=data.get("model"),
        )


def _collapse_integers(parts: list[str]) -> list[str]:
    if "int" in parts and "long" in parts:
        return ["int" if p == "int" else p for p in parts if p != "long"]
    return parts
