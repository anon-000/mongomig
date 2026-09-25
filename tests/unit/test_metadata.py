from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

import pymongo
import pytest
from beanie import Document, Indexed, PydanticObjectId
from pydantic import BaseModel

from mongomig import Index, MongoMetadata, collection
from mongomig.errors import ConfigError
from mongomig.metadata import registry
from mongomig.schema.normalize import collection_json_schema


@pytest.fixture(autouse=True)
def _fresh_default() -> Iterator[None]:
    registry._default = None
    yield
    registry._default = None


class User(BaseModel):
    name: str
    email: str
    age: int | None = None


def test_decorator_registers_into_default_and_env_py_configures_it() -> None:
    @collection("users", indexes=[Index("email", unique=True)])
    class U(BaseModel):
        email: str

    # env.py runs after models are imported: same registry, profile applied
    md = MongoMetadata.default(storage="json")
    assert md is MongoMetadata.default()
    assert "users" in md.collections
    assert md.profile.mode == "json"
    assert md.collections["users"].indexes[0].name == "email_1"


def test_register_explicit_and_duplicates() -> None:
    md = MongoMetadata()
    md.register(User, "users")
    md.register(User, "users")  # same model again (module re-import) is fine

    class Other(BaseModel):
        x: int

    with pytest.raises(ConfigError, match="registered twice"):
        md.register(Other, "users")
    with pytest.raises(TypeError, match="Pydantic"):
        md.register(dict, "nope")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        md.configure(storge="json")  # type: ignore[call-arg]


def test_index_declarations() -> None:
    assert Index("email").schema.name == "email_1"
    compound = Index([("a", 1), ("b", -1)], unique=True, partialFilterExpression={"x": 1})
    assert compound.schema.name == "a_1_b_-1"
    assert compound.schema.unique
    assert compound.schema.options_dict == {"partialFilterExpression": {"x": 1}}
    assert Index("body", name="search").schema.name == "search"


def test_duplicate_index_names_rejected() -> None:
    md = MongoMetadata()
    md.register(User, "users", indexes=[Index("email", name="x"), Index("name", name="x")])
    with pytest.raises(ConfigError, match="two indexes named 'x'"):
        md.schemas()


def test_validator_modes() -> None:
    md = MongoMetadata()
    md.register(User, "users", validator="auto", validation_level="strict")
    explicit = {"$jsonSchema": {"required": ["email"]}}

    class Order(BaseModel):
        total: float

    md.register(Order, "orders", validator=explicit)

    class Log(BaseModel):
        msg: str

    md.register(Log, "logs")
    schemas, _ = md.schemas()

    auto = schemas["users"].validator
    assert auto is not None
    body = auto["$jsonSchema"]
    assert body["required"] == ["_id", "name", "email", "age"]
    assert body["properties"]["age"] == {"bsonType": ["int", "long", "null"]}
    assert schemas["users"].validation_level == "strict"
    assert schemas["orders"].validator == explicit
    assert schemas["logs"].validator is None
    assert schemas["logs"].validation_level is None


def test_json_schema_nested_arrays_enums() -> None:
    from mongomig.schema.models import FieldSchema

    fields = {
        "tags": FieldSchema(
            bson_types=("array",), required=True, items=FieldSchema(bson_types=("string",))
        ),
        "status": FieldSchema(bson_types=("string",), nullable=True, enum=("a", "b")),
        "meta": FieldSchema(bson_types=("object",), open=True),
        "profile": FieldSchema(
            bson_types=("object",),
            fields={"verified": FieldSchema(bson_types=("bool",), required=True)},
        ),
        "any": FieldSchema(),
    }
    schema = collection_json_schema(fields)["$jsonSchema"]
    props = schema["properties"]
    assert schema["required"] == ["tags"]
    assert props["tags"] == {"bsonType": "array", "items": {"bsonType": "string"}}
    assert props["status"] == {"bsonType": ["string", "null"], "enum": ["a", "b", None]}
    assert props["meta"] == {"bsonType": "object"}
    assert props["profile"]["required"] == ["verified"]
    assert props["any"] == {}


# --- Beanie ------------------------------------------------------------------------------


class Product(Document):
    sku: Indexed(str, unique=True)  # type: ignore[valid-type]
    title: Annotated[str, Indexed()]
    price: float
    note: str | None = None

    class Settings:
        name = "products"
        indexes = [  # noqa: RUF012 (how Beanie users write it)
            "price",
            [("title", pymongo.TEXT)],
            pymongo.IndexModel([("price", 1), ("sku", -1)], name="price_sku", sparse=True),
        ]


class Plain(Document):
    value: int


class NoNulls(Document):
    maybe: str | None = None

    class Settings:
        keep_nulls = False
        use_revision = True


def test_beanie_document_registration() -> None:
    md = MongoMetadata()
    md.register_beanie(Product, Plain, NoNulls)
    schemas, warnings = md.schemas()
    assert warnings == []

    products = schemas["products"]
    assert products.model is not None
    assert products.model.endswith("Product")
    assert list(products.fields)[:2] == ["_id", "sku"]
    assert products.fields["_id"].bson_types == ("objectId",)
    assert products.fields["_id"].required
    assert not products.fields["_id"].nullable
    assert "revision_id" not in products.fields  # only stored when use_revision

    names = {ix.name: ix for ix in products.indexes}
    assert set(names) == {"price_1", "title_text", "price_sku", "sku_1", "title_1"}
    assert names["sku_1"].unique
    assert names["price_sku"].sparse
    assert names["price_sku"].keys == (("price", 1), ("sku", -1))

    assert "Plain" in schemas  # Beanie's default collection name is the class name
    no_nulls = schemas["NoNulls"]
    assert not no_nulls.fields["maybe"].required  # keep_nulls=False → None values omitted
    assert "revision_id" in no_nulls.fields


def test_register_beanie_rejects_plain_models() -> None:
    with pytest.raises(TypeError, match="beanie"):
        MongoMetadata().register_beanie(User)


def test_pydantic_object_id_maps_to_object_id() -> None:
    class M(BaseModel):
        owner: PydanticObjectId

    md = MongoMetadata()
    md.register(M, "m")
    assert md.schemas()[0]["m"].fields["owner"].bson_types == ("objectId",)
    md.configure(storage="json")
    assert md.schemas()[0]["m"].fields["owner"].bson_types == ("string",)
