# FastAPI

A complete app: migrations at startup (for development), and a readiness endpoint that keeps a
pod out of rotation until its database is migrated (for production).

```python
# app/main.py
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
```

- **`aupgrade_to_head()`** runs in a worker thread, so it doesn't block the event loop. With
  several workers, one takes the migration lock and migrates while the others wait, then
  find nothing to do.
- It **refuses migrations that can delete data** unless you pass `yes=True`. Those belong in a
  deploy step where a human (or `plan`) has looked at them.
- **`current_state()`** is read-only and cheap: one query on the tracking collection.

`/health/ready` returns `{"ready": true, "pending": [], "failed": []}` when migrated, and `503`
otherwise. Point a Kubernetes `readinessProbe` at it (see [Deploy](deploy.md)).

!!! tip "Production"
    Many teams keep the readiness check but drop the migration from `lifespan`, running
    `mongomig upgrade` as a deploy step instead. New pods then simply wait until the
    database is ready.

This app and its tests run in MongoMig's own test setup; see [Test your migrations](testing.md).
