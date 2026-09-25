"""FastAPI + Beanie. Migrations run as a separate step (`mongomig upgrade`), not at startup.

Run:
    export MONGODB_URI=mongodb://localhost:27017/?directConnection=true
    mongomig upgrade
    uvicorn catalog.main:app --reload
"""

import os
from contextlib import asynccontextmanager

from beanie import init_beanie
from fastapi import FastAPI
from pymongo import AsyncMongoClient

from catalog.models import DOCUMENTS, Product

client: AsyncMongoClient = AsyncMongoClient(os.environ["MONGODB_URI"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Indexes are managed by MongoMig migrations, so Beanie doesn't need to create them.
    await init_beanie(
        database=client[os.environ.get("MONGODB_DATABASE", "catalog")],
        document_models=DOCUMENTS,
        skip_indexes=True,
    )
    yield
    await client.close()


app = FastAPI(lifespan=lifespan)


@app.post("/products", status_code=201)
async def create_product(product: Product) -> Product:
    return await product.insert()


@app.get("/products")
async def list_products() -> list[Product]:
    return await Product.find_all().limit(100).to_list()
