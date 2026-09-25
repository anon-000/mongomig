from __future__ import annotations

import datetime
import decimal
import enum
import uuid
from typing import Annotated, Any, Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from mongomig.metadata.registry import MongoMetadata
from mongomig.schema.models import FieldSchema


def fields_of(model: type[BaseModel], **profile: Any) -> tuple[dict[str, FieldSchema], list[str]]:
    md = MongoMetadata(**profile)
    md.register(model, "c")
    schemas, warnings = md.schemas()
    return schemas["c"].fields, warnings


class Color(str, enum.Enum):  # noqa: UP042 (common pattern in user code)
    RED = "red"
    BLUE = "blue"


class Everything(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    s: str
    i: int
    f: float
    b: bool
    dt: datetime.datetime
    dec: decimal.Decimal
    u: uuid.UUID
    raw: bytes
    color: Color
    lit: Literal["a", "b"]
    lit_num: Literal[1, 2]
    anything: Any
    mapping: dict[str, int]
    strings: list[str]
    pair: tuple[int, str]
    many: tuple[int, ...]


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (
            "python",
            {"dt": ("date",), "dec": ("decimal",), "u": ("binData",), "raw": ("binData",)},
        ),
        (
            "json",
            {"dt": ("string",), "dec": ("string",), "u": ("string",), "raw": ("string",)},
        ),
        (
            "beanie",
            {"dt": ("date",), "dec": ("decimal",), "u": ("binData",), "raw": ("binData",)},
        ),
    ],
)
def test_types_depend_on_storage_mode(mode: str, expected: dict[str, tuple[str, ...]]) -> None:
    fields, _ = fields_of(Everything, storage=mode)
    for name, bson in expected.items():
        assert fields[name].bson_types == bson, name


def test_scalar_and_container_mapping() -> None:
    fields, _ = fields_of(Everything)
    assert fields["_id"].bson_types == ("objectId",)
    assert fields["s"].bson_types == ("string",)
    assert fields["i"].bson_types == ("int", "long")  # one integer family
    assert fields["f"].bson_types == ("double",)
    assert fields["b"].bson_types == ("bool",)
    assert fields["color"].enum == ("red", "blue")
    assert fields["color"].bson_types == ("string",)
    assert fields["lit"].enum == ("a", "b")
    assert fields["lit_num"].bson_types == ("int", "long")
    assert fields["anything"].bson_types == ()
    assert fields["mapping"].open is True
    assert fields["mapping"].fields is None
    assert fields["strings"].items is not None
    assert fields["strings"].items.bson_types == ("string",)
    assert fields["pair"].items is not None
    assert set(fields["pair"].items.bson_types) == {"int", "long", "string"}
    assert fields["many"].items is not None
    assert fields["many"].items.bson_types == ("int", "long")


def test_python_mode_warns_about_unstorable_types() -> None:
    _, warnings = fields_of(Everything, storage="python")
    text = "\n".join(warnings)
    assert "c.dec: Decimal" in text
    assert "c.u: UUID" in text
    assert "Color enum" not in text  # use_enum_values=True makes enums storable

    class NoEnumValues(BaseModel):
        color: Color
        day: datetime.date
        site: HttpUrl

    _, warnings = fields_of(NoEnumValues, storage="python")
    text = "\n".join(warnings)
    assert "Color enum members cannot be encoded" in text
    assert "datetime.date cannot be encoded" in text
    assert "HttpUrl" in text

    _, json_warnings = fields_of(NoEnumValues, storage="json")
    assert json_warnings == []


def test_required_and_nullable_rules() -> None:
    class M(BaseModel):
        needed: str
        with_default: str = "x"
        optional: int | None = None
        nullable_required: int | None

    default, _ = fields_of(M)
    assert all(f.required for f in default.values())  # model_dump() writes every field
    assert default["optional"].nullable
    assert not default["needed"].nullable
    assert default["with_default"].default == "x"
    assert default["optional"].has_default
    assert default["optional"].default is None
    assert not default["needed"].has_default

    exclude_none, _ = fields_of(M, exclude_none=True)
    assert not exclude_none["optional"].required
    assert exclude_none["with_default"].required

    exclude_unset, _ = fields_of(M, exclude_unset=True)
    assert exclude_unset["needed"].required
    assert exclude_unset["nullable_required"].required
    assert not exclude_unset["with_default"].required


def test_aliases() -> None:
    class M(BaseModel):
        id: str = Field(alias="_id")
        full_name: str = Field(alias="fullName")
        other: str = Field(serialization_alias="o")

    by_alias, _ = fields_of(M)
    assert list(by_alias) == ["_id", "fullName", "o"]
    assert by_alias["_id"].bson_types == ("string",)  # declared _id type wins
    no_alias, _ = fields_of(M, by_alias=False)
    assert "full_name" in no_alias


def test_nested_recursive_and_union_models() -> None:
    class Address(BaseModel):
        city: str
        zip: str | None = None

    class Node(BaseModel):
        name: str
        children: list[Node] = []

    class Cat(BaseModel):
        meow: bool

    class Dog(BaseModel):
        bark: bool

    class M(BaseModel):
        address: Address
        maybe_address: Address | None = None
        tree: Node
        pet: Cat | Dog

    fields, _ = fields_of(M)
    address = fields["address"]
    assert address.bson_types == ("object",)
    assert address.fields is not None
    assert list(address.fields) == ["city", "zip"]
    assert fields["maybe_address"].nullable
    assert fields["maybe_address"].fields is not None

    tree = fields["tree"]
    assert tree.fields is not None
    items = tree.fields["children"].items
    assert items is not None
    assert items.open  # recursion stops instead of looping forever

    assert fields["pet"].open  # union of different models: shape not enforced


def test_type_overrides() -> None:
    class Money:
        pass

    class M(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True)
        price: Money

    _, warnings = fields_of(M)
    assert any("unknown type" in w for w in warnings)  # without an override: flagged

    fields, _ = fields_of(M, type_overrides={Money: "decimal"})
    assert fields["price"].bson_types == ("decimal",)


def test_static_and_dynamic_defaults() -> None:
    class M(BaseModel):
        a: list[int] = Field(default_factory=list)
        b: datetime.datetime = Field(default_factory=datetime.datetime.now)
        c: Color = Color.RED
        d: dict[str, int] = {}

    fields, _ = fields_of(M, storage="json")
    assert (fields["a"].default_is_static, fields["a"].default) == (True, [])
    assert fields["b"].has_default
    assert not fields["b"].default_is_static
    assert fields["c"].default == "red"
    assert fields["d"].default == {}


def test_annotated_constraints_are_unwrapped() -> None:
    class M(BaseModel):
        short: Annotated[str, Field(max_length=5)]
        count: Annotated[int, Field(ge=0)] = 0

    fields, _ = fields_of(M)
    assert fields["short"].bson_types == ("string",)
    assert fields["count"].bson_types == ("int", "long")


def test_type_label() -> None:
    assert FieldSchema(bson_types=("int", "long"), nullable=True).type_label() == "int | null"
    arr = FieldSchema(bson_types=("array",), items=FieldSchema(bson_types=("string",)))
    assert arr.type_label() == "array<string>"
    assert FieldSchema().type_label() == "any"
