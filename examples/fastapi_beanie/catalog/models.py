"""Beanie documents. MongoMig reads their collection names and indexes directly."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

import pymongo
from beanie import Document, Indexed
from pydantic import Field


class Product(Document):
    sku: Indexed(str, unique=True)  # type: ignore[valid-type]
    title: Annotated[str, Indexed()]
    price: Decimal
    tags: list[str] = []
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "products"
        indexes = [  # noqa: RUF012 (Beanie's convention)
            [("title", pymongo.TEXT)],
            pymongo.IndexModel([("price", 1), ("created_at", -1)], name="price_recent"),
        ]


class Review(Document):
    product_sku: str
    rating: int
    body: str | None = None

    class Settings:
        name = "reviews"
        indexes = ["product_sku"]  # noqa: RUF012


DOCUMENTS = [Product, Review]
