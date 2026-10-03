# A long custom data migration

**Situation:** each order has `full_name: "Ada Lovelace"` and should get
`customer: {first, last}` instead. That's custom logic `ctx.ops` can't express, on 200,000
documents, and it must be safe to interrupt.

## The migration

```python
def upgrade(ctx):
    from pymongo import UpdateOne
    
    orders = ctx.collection("orders")
    for batch in ctx.batches("orders", {"customer": {"$exists": False}},
                             projection={"full_name": 1}, batch_size=1000, transactional=True):
        updates = []
        for order in batch:
            first, _, last = order["full_name"].partition(" ")
            updates.append(UpdateOne(
                {"_id": order["_id"]},
                {"$set": {"customer": {"first": first, "last": last}}, "$unset": {"full_name": ""}},
            ))
        orders.bulk_write(updates, session=batch.session)


def downgrade(ctx):
    pass
```

- **`ctx.batches(...)`** walks the matching documents in `_id` order and checkpoints after each
  batch. If the run dies, `mongomig resume` continues after the last completed batch.
- **`bulk_write` per batch** is one round trip per 1,000 documents instead of 1,000 round trips.
  In this example it was 10× faster than `update_one` per document (about 43,000 vs 4,400
  documents/s).
- **`transactional=True`** commits each batch's writes together with its checkpoint, so a
  crash never leaves a half-processed batch (exactly-once). Pass `session=batch.session` to
  the writes. It needs a replica set (a single-node one is fine).
- The filter `{"customer": {"$exists": False}}` makes it re-runnable even without
  checkpoints.

## Plan it

```console
$ mongomig plan
Migration plan for orders_db

  ab4e4d589e2a  totals as numbers                        applied
  0e7e4fd9869b  split customer names                     pending

0e7e4fd9869b  split customer names   Risk: MEDIUM
  orders  batches     custom loop (first batch simulated)  ~200,000 docs · collection scan · custom code
  orders  bulk_write  1000 operations                      ~1,000 docs · custom code
  reversible: yes · deletes data: no · resumable: yes (checkpointed)
  why MEDIUM: ~200k documents (orders.batches); custom code: document counts are estimates

1 migration(s) · ~200,000 document writes · highest risk MEDIUM
Estimates only; nothing was changed.
```

In a dry run only the first batch is simulated, so `plan` stays fast; the loop's size comes
from a document count.

## Run it

```console
$ mongomig upgrade
Running upgrade ab4e4d589e2a -> 0e7e4fd9869b, split customer names
  batches orders: 80,000 / ~200,000 (40.0%) · 39,957 docs/s · elapsed 2.0s · ETA 3.0s
  batches orders: 162,000 / ~200,000 (81.0%) · 40,440 docs/s · elapsed 4.0s · ETA 940ms
  batches orders: 200,000 / ~200,000 (100.0%) · 40,314 docs/s · elapsed 5.0s
  • batches orders: 200,000 documents in 200 batches
  ✓ done in 5.0s
Applied 1 revision(s).
```

Result: `orders with customer: 200000, with full_name left: 0`.

!!! tip "Without transactions"
    Drop `transactional=True` on a standalone server. A batch interrupted mid-way is then
    processed again on resume (at-least-once), so keep the per-document work repeatable, as
    the filter above does.
