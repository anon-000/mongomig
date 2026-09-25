"""A small FastAPI app using PyMongo's async client and MongoMig at startup.

Run:
    export MONGODB_URI=mongodb://localhost:27017/?directConnection=true
    uvicorn userservice.main:app --reload
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pymongo import AsyncMongoClient
from pymongo.errors import DuplicateKeyError

from mongomig import aupgrade_to_head
from userservice.models import User

client: AsyncMongoClient = AsyncMongoClient(os.environ["MONGODB_URI"])
db = client[os.environ.get("MONGODB_DATABASE", "userservice")]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Development convenience: apply pending migrations on startup. Every worker may call
    # this; one migrates while the others wait. In production run `mongomig upgrade` as a
    # deploy step instead.
    await aupgrade_to_head()
    yield
    await client.close()


app = FastAPI(lifespan=lifespan)


@app.post("/users", status_code=201)
async def create_user(user: User) -> User:
    try:
        await db.users.insert_one(user.model_dump())  # storage="python" in env.py
    except DuplicateKeyError:
        raise HTTPException(409, "email already registered") from None
    return user


@app.get("/users")
async def list_users() -> list[User]:
    return [User(**doc) async for doc in db.users.find({}, {"_id": 0}).limit(100)]
