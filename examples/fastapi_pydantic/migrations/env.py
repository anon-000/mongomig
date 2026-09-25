"""MongoMig environment.

Plain Python, executed with your project root on sys.path, so you can import your
application's models here (just like Alembic's env.py).

`target_metadata` tells MongoMig which collections your models describe. Register them in one
of three ways:

1. Decorate plain Pydantic models (in your models module):

    from mongomig import collection, Index

    @collection("users", indexes=[Index("email", unique=True)])
    class User(BaseModel):
        name: str
        email: str

   ...then import that module below so the decorators run.

2. Register explicitly here (keeps MongoMig out of your models):

    target_metadata.register(User, "users", indexes=[Index("email", unique=True)])

3. Beanie documents (collection name and indexes are read from the Document):

    target_metadata.register_beanie(User, Order)

`storage` must match how your app writes documents:
    "python" - coll.insert_one(model.model_dump())
    "json"   - model.model_dump(mode="json") / FastAPI jsonable_encoder (dates stored as strings)
    "beanie" - Beanie (set automatically for register_beanie models)

Check the result with:  mongomig models
"""

from mongomig import MongoMetadata

import userservice.models  # noqa: F401  (importing registers @collection models)

target_metadata = MongoMetadata.default(storage="python", by_alias=True)
