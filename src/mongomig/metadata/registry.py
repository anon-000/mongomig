"""``MongoMetadata``: the registry of collections MongoMig manages (like Alembic's ``MetaData``).

Three ways to fill it, all ending in ``register()``:

    # 1. decorator on plain Pydantic models
    @collection("users", indexes=[Index("email", unique=True)])
    class User(BaseModel): ...

    # 2. explicit registration (keeps MongoMig out of your model modules)
    metadata.register(User, "users", indexes=[Index("email", unique=True)])

    # 3. Beanie documents: name and indexes are read from the Document itself
    metadata.register_beanie(User, Order)

The diff engine only ever sees ``metadata.schemas()``: normalised ``CollectionSchema``s.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, TypeVar

from mongomig.errors import ConfigError
from mongomig.schema.indexes import IndexKeys, build_index
from mongomig.schema.models import IndexSchema

if TYPE_CHECKING:
    from pydantic import BaseModel

    from mongomig.schema.models import CollectionSchema

StorageMode = Literal["python", "json", "beanie"]
ValidatorSpec = Literal["auto"] | Mapping[str, Any] | None
M = TypeVar("M", bound="type[BaseModel]")


class Index:
    """An index declaration: ``Index("email", unique=True)``, ``Index([("a", 1), ("b", -1)])``.

    Extra keyword options go to MongoDB as-is: ``partialFilterExpression``,
    ``expireAfterSeconds``, ``collation``, ``hidden``, ...
    """

    def __init__(
        self,
        keys: IndexKeys,
        *,
        name: str | None = None,
        unique: bool = False,
        sparse: bool = False,
        **options: Any,
    ) -> None:
        self.schema: IndexSchema = build_index(
            keys, name=name, unique=unique, sparse=sparse, **options
        )

    def __repr__(self) -> str:
        return f"Index({self.schema.describe()})"


@dataclass(frozen=True)
class StorageProfile:
    """How the application writes documents, which decides the stored BSON types.

    - ``python``: ``coll.insert_one(model.model_dump())`` (datetime → date, ...)
    - ``json``: ``model.model_dump(mode="json")`` / FastAPI ``jsonable_encoder`` (datetime,
      UUID, Decimal, ObjectId → string)
    - ``beanie``: Beanie's encoder (datetime/date → date, UUID → binData, Decimal → decimal)
    """

    mode: StorageMode = "python"
    by_alias: bool = True
    exclude_none: bool = False
    exclude_unset: bool = False
    type_overrides: Mapping[type, str | tuple[str, ...]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "by_alias": self.by_alias,
            "exclude_none": self.exclude_none,
            "exclude_unset": self.exclude_unset,
            "type_overrides": {
                f"{t.__module__}.{t.__qualname__}": v for t, v in self.type_overrides.items()
            },
        }


@dataclass
class CollectionDef:
    name: str
    model: type[BaseModel]
    indexes: tuple[IndexSchema, ...] = ()
    validator: ValidatorSpec = None
    validation_level: str = "moderate"
    validation_action: str = "error"
    source: Literal["pydantic", "beanie"] = "pydantic"

    @property
    def model_path(self) -> str:
        return f"{self.model.__module__}.{self.model.__qualname__}"


_default: MongoMetadata | None = None


class MongoMetadata:
    def __init__(
        self,
        *,
        storage: StorageMode = "python",
        by_alias: bool = True,
        exclude_none: bool = False,
        exclude_unset: bool = False,
        type_overrides: Mapping[type, str | tuple[str, ...]] | None = None,
    ) -> None:
        self.profile = StorageProfile(
            mode=storage,
            by_alias=by_alias,
            exclude_none=exclude_none,
            exclude_unset=exclude_unset,
            type_overrides=dict(type_overrides or {}),
        )
        self.collections: dict[str, CollectionDef] = {}

    @classmethod
    def default(cls, **profile: Any) -> MongoMetadata:
        """The process-wide registry that ``@collection`` fills.

        Call it from ``env.py`` with storage-profile options, e.g.
        ``MongoMetadata.default(storage="json")``; models imported earlier stay registered.
        """
        global _default  # noqa: PLW0603
        if _default is None:
            _default = cls(**profile)
        elif profile:
            _default.configure(**profile)
        return _default

    def configure(
        self,
        *,
        storage: StorageMode | None = None,
        by_alias: bool | None = None,
        exclude_none: bool | None = None,
        exclude_unset: bool | None = None,
        type_overrides: Mapping[type, str | tuple[str, ...]] | None = None,
    ) -> MongoMetadata:
        """Change storage-profile options; unspecified options keep their value."""
        p = self.profile
        self.profile = StorageProfile(
            mode=p.mode if storage is None else storage,
            by_alias=p.by_alias if by_alias is None else by_alias,
            exclude_none=p.exclude_none if exclude_none is None else exclude_none,
            exclude_unset=p.exclude_unset if exclude_unset is None else exclude_unset,
            type_overrides=dict(p.type_overrides if type_overrides is None else type_overrides),
        )
        return self

    # --- registration --------------------------------------------------------------------

    def register(
        self,
        model: type[BaseModel],
        name: str,
        *,
        indexes: Iterable[Index | IndexSchema] = (),
        validator: ValidatorSpec = None,
        validation_level: str = "moderate",
        validation_action: str = "error",
    ) -> CollectionDef:
        """Register ``model`` as the shape of collection ``name``.

        ``validator``: ``None`` (MongoMig leaves validators alone), ``"auto"`` (generate
        ``$jsonSchema`` from the model), or an explicit validator document.
        """
        return self._add(
            CollectionDef(
                name=name,
                model=_check_model(model),
                indexes=tuple(_to_index_schema(ix) for ix in indexes),
                validator=validator,
                validation_level=validation_level,
                validation_action=validation_action,
            )
        )

    def register_beanie(self, *documents: type[BaseModel]) -> list[CollectionDef]:
        """Register Beanie ``Document`` classes (name/indexes come from the class)."""
        from mongomig.metadata.beanie import collection_def_from_beanie

        return [self._add(collection_def_from_beanie(doc)) for doc in documents]

    def schemas(self) -> tuple[dict[str, CollectionSchema], list[str]]:
        """Normalised declared schemas, plus type-mapping warnings."""
        from mongomig.schema.normalize import metadata_to_schemas

        return metadata_to_schemas(self)

    def _add(self, definition: CollectionDef) -> CollectionDef:
        existing = self.collections.get(definition.name)
        if existing is not None and existing.model_path != definition.model_path:
            raise ConfigError(
                f"Collection {definition.name!r} is registered twice: by "
                f"{existing.model_path} and {definition.model_path}.",
                suggestion="Each collection must map to exactly one model.",
            )
        self.collections[definition.name] = definition
        return definition


def collection(
    name: str,
    *,
    indexes: Iterable[Index | IndexSchema] = (),
    validator: ValidatorSpec = None,
    validation_level: str = "moderate",
    validation_action: str = "error",
    metadata: MongoMetadata | None = None,
) -> Callable[[M], M]:
    """Class decorator: register a Pydantic model as the shape of a collection."""

    def decorate(model: M) -> M:
        (metadata or MongoMetadata.default()).register(
            model,
            name,
            indexes=indexes,
            validator=validator,
            validation_level=validation_level,
            validation_action=validation_action,
        )
        return model

    return decorate


def _to_index_schema(index: Index | IndexSchema) -> IndexSchema:
    return index.schema if isinstance(index, Index) else index


def _check_model(model: Any) -> type[BaseModel]:
    from pydantic import BaseModel

    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        raise TypeError(f"{model!r} is not a Pydantic v2 BaseModel subclass")
    return model
