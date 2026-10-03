import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from pymongo import AsyncMongoClient

import mongomig
from app.models import Order

client = AsyncMongoClient(os.environ["MONGODB_URI"])
db = client[os.environ.get("MONGODB_DATABASE", "store")]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Development convenience: apply pending migrations at startup. Every worker may call
    # this; one migrates while the others wait for the lock. In production, prefer running
    # `mongomig upgrade` as a deploy step and keep only the readiness check below.
    await mongomig.aupgrade_to_head()
    yield
    await client.close()


app = FastAPI(lifespan=lifespan)


@app.get("/health/ready")
async def ready(response: Response) -> dict[str, object]:
    """Ready only when the database is fully migrated (use as a Kubernetes readinessProbe)."""
    state = await asyncio.to_thread(mongomig.current_state)
    is_ready = not state.pending and not state.failed
    response.status_code = 200 if is_ready else 503
    return {"ready": is_ready, "pending": state.pending, "failed": state.failed}


@app.post("/orders", status_code=201)
async def create_order(order: Order) -> Order:
    await db.orders.insert_one(order.model_dump())
    return order
