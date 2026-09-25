"""Plain Pydantic models, registered with MongoMig's @collection decorator."""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from mongomig import Index, collection


class Profile(BaseModel):
    bio: str | None = None
    email_verified: bool = False


@collection(
    "users",
    indexes=[Index("email", unique=True, name="users_email_unique"), Index("role")],
    validator="auto",
)
class User(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    name: str
    email: str
    status: str = "active"
    role: str = "member"
    phone: str | None = None
    profile: Profile = Field(default_factory=Profile)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


@collection("orders", indexes=[Index([("user_email", 1), ("created_at", -1)])])
class Order(BaseModel):
    user_email: str
    total: float
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
