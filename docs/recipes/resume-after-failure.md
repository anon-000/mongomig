# A migration failed halfway

**Situation:** a migration converting 200,000 order totals from strings to numbers stops at
document 120,000: one value is `"12,50"`, which isn't a number. Production is half
migrated. What now?

The migration:

```python
def upgrade(ctx):
    # totals were stored as strings; convert them to numbers
    ctx.ops.backfill(
        "orders",
        {"total": {"$type": "string"}},
        [{"$set": {"total": {"$toDouble": "$total"}}}],
    )


def downgrade(ctx):
    pass
```

## 1. It fails, and tells you where

```console
$ mongomig upgrade
Running upgrade <base> -> ab4e4d589e2a, totals as numbers
  backfill orders: 116,000 / ~200,000 (58.0%) · 57,801 docs/s · elapsed 2.0s · ETA 1.5s
  ✗ failed
error: Migration ab4e4d589e2a (totals as numbers) failed during upgrade in backfill on orders after 120,000 documents: WriteError: Failed to parse number '12,50' in $convert with no onError value: Did not consume whole string.
hint: Fix the cause, then `mongomig resume`: completed batches are skipped and it continues where it stopped.
```

Nothing is lost: the 120,000 converted documents stay converted, and progress was saved after
every batch (a **checkpoint**).

## 2. See the state

```console
$ mongomig current
Database: orders_db
Current:  <base> (no revisions applied)
Pending:  1
  - ab4e4d589e2a  totals as numbers
Failed:   ab4e4d589e2a: upgrade: WriteError: Failed to parse number '12,50' in $convert with no onError value: Did not consume whole string.
          resumes from checkpoint: orders (120,000 documents done)
Fix the cause, then `mongomig resume` (or `upgrade`).
```

## 3. Fix the cause

Here it's the data:

```python
db.orders.update_one({"total": "12,50"}, {"$set": {"total": "12.50"}})
```

(If the migration was wrong instead, you may edit it: it hasn't been applied. Its checkpoints
are then discarded and it starts over, so make sure re-processing is safe.)

## 4. Resume

```console
$ mongomig resume
Running upgrade <base> -> ab4e4d589e2a, totals as numbers
  • backfill orders: resuming after 120,000 documents (checkpoint)
  • backfill orders: 200,000 modified (200,000 matched, 200 batches)
  ✓ done in 1.5s
Applied 1 revision(s).
```

It continued after document 120,000; the completed batches weren't redone.

!!! note "What resumes"
    Every `ctx.ops` operation and every [`ctx.batches`](long-data-migration.md) loop
    checkpoints after each batch. Writes done with raw `ctx.collection(...)` calls
    *outside* `ctx.batches` aren't checkpointed: they run again from the start, so make them
    re-runnable (filter on "not yet migrated").
