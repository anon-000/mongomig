"""Registered models → declared ``CollectionSchema``s (+ generated ``$jsonSchema`` validators)."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from mongomig.errors import ConfigError
from mongomig.schema.models import CollectionSchema, FieldSchema

if TYPE_CHECKING:
    from mongomig.metadata.registry import CollectionDef, MongoMetadata, StorageProfile

ID_FIELD = "_id"


def metadata_to_schemas(
    metadata: MongoMetadata,
) -> tuple[dict[str, CollectionSchema], list[str]]:
    schemas: dict[str, CollectionSchema] = {}
    warnings: list[str] = []
    for name in sorted(metadata.collections):
        schema, collection_warnings = collection_schema(
            metadata.collections[name], metadata.profile
        )
        schemas[name] = schema
        warnings.extend(collection_warnings)
    return schemas, warnings


def collection_schema(
    definition: CollectionDef, profile: StorageProfile
) -> tuple[CollectionSchema, list[str]]:
    from mongomig.metadata.beanie import (
        beanie_keep_nulls,
        beanie_uses_revision,
        indexed_field_index,
    )
    from mongomig.metadata.typemap import MappingContext, model_fields_schema

    is_beanie = definition.source == "beanie"
    if is_beanie:
        profile = replace(
            profile,
            mode="beanie",
            by_alias=True,
            exclude_none=not beanie_keep_nulls(definition.model),
        )

    ctx = MappingContext(profile=profile)
    fields = model_fields_schema(definition.model, ctx)
    if is_beanie and beanie_uses_revision(definition.model):
        # Excluded from Beanie's dump but written separately when use_revision=True.
        fields["revision_id"] = FieldSchema(bson_types=("binData",), required=True, nullable=True)
    fields = _with_id(fields)

    indexes = list(definition.indexes)
    for path, index_type, kwargs in ctx.indexed_fields:
        indexes.append(indexed_field_index(path, index_type, kwargs))
    _check_unique_index_names(definition.name, indexes)

    validator: dict[str, Any] | None
    if definition.validator == "auto":
        validator = collection_json_schema(fields)
    elif definition.validator is None:
        validator = None
    else:
        validator = dict(definition.validator)

    schema = CollectionSchema(
        name=definition.name,
        fields=fields,
        indexes=indexes,
        validator=validator,
        validation_level=definition.validation_level if validator is not None else None,
        validation_action=definition.validation_action if validator is not None else None,
        source="declared",
        model=definition.model_path,
    )
    return schema, [f"{definition.name}.{w}" for w in ctx.warnings]


def _with_id(fields: dict[str, FieldSchema]) -> dict[str, FieldSchema]:
    """Every stored document has a non-null ``_id``; default to ObjectId when undeclared."""
    id_field = fields.pop(ID_FIELD, None)
    if id_field is None:
        id_field = FieldSchema(bson_types=("objectId",))
    else:
        id_field.nullable = False
        if not id_field.bson_types:
            id_field.bson_types = ("objectId",)
    id_field.required = True
    id_field.has_default = False
    id_field.default = None
    id_field.default_is_static = False
    return {ID_FIELD: id_field, **fields}


def _check_unique_index_names(collection: str, indexes: list[Any]) -> None:
    seen: set[str] = set()
    for index in indexes:
        if index.name in seen:
            raise ConfigError(
                f"Collection {collection!r} declares two indexes named {index.name!r}.",
                suggestion="Give each index a unique name.",
            )
        seen.add(index.name)


# --- $jsonSchema generation --------------------------------------------------------------


def collection_json_schema(fields: dict[str, FieldSchema]) -> dict[str, Any]:
    """``{"$jsonSchema": ...}`` validator describing ``fields``."""
    return {"$jsonSchema": _object_json_schema(fields)}


def _object_json_schema(fields: dict[str, FieldSchema]) -> dict[str, Any]:
    schema: dict[str, Any] = {"bsonType": "object"}
    required = [name for name, f in fields.items() if f.required]
    if required:
        schema["required"] = required
    schema["properties"] = {name: field_json_schema(f) for name, f in fields.items()}
    return schema


def field_json_schema(field: FieldSchema) -> dict[str, Any]:
    if not field.bson_types:  # any type
        return {}
    types: list[str] = []
    for t in field.bson_types:
        if t not in types:
            types.append(t)
    if field.nullable:
        types.append("null")
    out: dict[str, Any] = {"bsonType": types[0] if len(types) == 1 else types}
    if field.fields is not None and not field.open:
        out.update({k: v for k, v in _object_json_schema(field.fields).items() if k != "bsonType"})
    if field.items is not None and field.items.bson_types:
        out["items"] = field_json_schema(field.items)
    if field.enum is not None:
        out["enum"] = [*field.enum, None] if field.nullable else list(field.enum)
    return out
