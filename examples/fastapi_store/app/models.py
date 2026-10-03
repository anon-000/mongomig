from pydantic import BaseModel

from mongomig import Index, collection


@collection("orders", indexes=[Index([("customer.last", 1), ("customer.first", 1)])])
class Order(BaseModel):
    total: float
    customer: dict[str, str] = {}
