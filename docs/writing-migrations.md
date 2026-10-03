# Writing migrations

## Anatomy

```python
"""add user status

Revision: 7be204a1c9e0
Revises: a1f3c9d20b44
Created: 2026-09-25 10:12:00 UTC
"""

revision = "7be204a1c9e0"
down_revision = "a1f3c9d20b44"   # None for the first; a tuple for merges
branch_labels = None
depends_on = None
reversible = True                # False: downgrade refuses to pass through (unless --force)
snapshot_hash = "sha256:..."     # set by MongoMig
mongomig_format = 1


def upgrade(ctx):
    ctx.ops.backfill("users", {"status": {"$exists": False}}, {"$set": {"status": "active"}})


def downgrade(ctx):
    ctx.ops.unset_field("users", "status")
```

Create one with `mongomig revision -m "..."` (empty) or `--autogenerate`. Metadata must be
literal values (MongoMig reads them without importing the file). Once applied, **don't edit a
revision**: its checksum is recorded and `upgrade` stops on a mismatch.

## `ctx`

| | |
|---|---|
| `ctx.ops` | high-level operations (below): idempotent, batched, dry-run aware. Prefer these. |
| `ctx.collection(name)` | a PyMongo `Collection` for custom logic |
| `ctx.batches(coll, filter, ...)` | resumable loop over documents in `_id` order (see [Custom logic](#custom-logic)) |
| `ctx.transaction()` | `with ctx.transaction() as session:` atomic writes (replica set required) |
| `ctx.unsafe_db` | the raw `Database`; dry runs can't simulate it |
| `ctx.log(msg)` | a line in the progress output |
| `ctx.dry_run` | `True` during `plan` / `--dry-run` |
| `ctx.revision`, `ctx.direction`, `ctx.environment` | context information |

## `ctx.ops` reference

### Data

| Operation | Notes |
|---|---|
| `backfill(coll, filter, update, batch_size=None)` | `update_many` in `_id`-ordered batches. `update` may be an aggregation pipeline. Write `filter` so migrated documents stop matching; then re-running is safe. |
| `unset_field(coll, field, filter=None, backup=False)` | removes a (dotted) field. `backup=True` first copies values to `__mongomig_backup_<revision>`. |
| `restore_field(coll, field)` | puts back values saved by `unset_field(..., backup=True)` in the same revision |
| `rename_field(coll, old, new, filter=None)` | `$rename`, batched |

Batches use `execution.batch_size`, sleep `execution.sleep_ms_between_batches` between
batches, retry transient errors (`execution.max_retries`, exponential backoff) and report
progress with rate and ETA.

### Collections

| Operation | Notes |
|---|---|
| `create_collection(name, validator=None, validation_level="moderate", validation_action="error", **options)` | skipped if it exists |
| `drop_collection(name, backup=False)` | `backup=True` renames it to `__mongomig_backup_<revision>_<name>` instead |
| `restore_collection(name)` | undoes `drop_collection(name, backup=True)` |
| `rename_collection(old, new, drop_target=False)` | |

### Indexes and validators

| Operation | Notes |
|---|---|
| `create_index(coll, keys, name=None, **options)` | `keys`: `"email"`, `["a", "b"]`, `[("a", 1), ("b", -1)]`, `{"loc": "2dsphere"}`. Options: `unique`, `sparse`, `partialFilterExpression`, `expireAfterSeconds`, `collation`, `hidden`... Idempotent for identical definitions. |
| `drop_index(coll, name)` | skipped if missing |
| `set_validator(coll, validator, level="moderate", action="error")` | creates the collection if needed |
| `remove_validator(coll)` | |

## Custom logic

When `ctx.ops` doesn't fit, loop with `ctx.batches`:

```python
def upgrade(ctx):
    users = ctx.collection("users")
    for batch in ctx.batches("users", {"full_name": {"$exists": True}},
                             projection={"full_name": 1}, batch_size=500):
        for user in batch:
            first, _, last = user["full_name"].partition(" ")
            users.update_one(
                {"_id": user["_id"]},
                {"$set": {"first_name": first, "last_name": last or None},
                 "$unset": {"full_name": ""}},
            )
```

`ctx.batches(collection, filter=None, *, projection=None, batch_size=None, transactional=False)`
walks the matching documents in `_id` order and **checkpoints after every batch**. If the
migration fails (or the process is killed), `mongomig resume` (or `upgrade`) continues after
the last completed batch instead of starting over. Each batch is a list of documents with
`.number` and `.session`.

- **At-least-once (default):** a batch interrupted mid-way is processed again, so per-document
  work should be repeatable (as above: the filter skips migrated documents).
- **Exactly-once:** `transactional=True` runs each batch in a transaction together with its
  checkpoint. Pass `session=batch.session` to every write:

  ```python
  for batch in ctx.batches("accounts", transactional=True):
      for acc in batch:
          ctx.collection("accounts").update_one(
              {"_id": acc["_id"]}, {"$inc": {"credits": 10}}, session=batch.session
          )
  ```

  Keep transactional batches small (a few hundred documents): a transaction should finish
  well within MongoDB's 60-second limit.

In dry runs (`plan`, `--dry-run`) only the first batch is simulated, so planning stays fast.

### Transactions

```python
def upgrade(ctx):
    with ctx.transaction() as session:
        ctx.collection("accounts").update_one({"_id": a}, {"$inc": {"bal": -10}}, session=session)
        ctx.collection("accounts").update_one({"_id": b}, {"$inc": {"bal": 10}}, session=session)
```

The block commits when it ends and aborts if it raises. It needs a replica set or a sharded
cluster (a single-node replica set is fine); on a standalone server it fails with a clear error.
Writes without `session=` are not part of the transaction. Migrations are **not** wrapped in a
transaction automatically: MongoDB transactions are limited in size and duration, which
doesn't suit bulk data changes.

### Guidelines

- **Make it re-runnable.** Checkpoints skip completed batches, but the batch that was in
  flight runs again (unless `transactional=True`). Filtering on "not yet migrated" makes any
  re-run safe.
- **No side effects outside `ctx`.** `plan`/`--dry-run` run your function for real with writes
  intercepted; an HTTP call or email would happen.
- **Don't edit a failed migration lightly.** Editing it is allowed (it hasn't been applied),
  but its checkpoints are then discarded and it starts over.

## Reversibility

Write a `downgrade` that undoes `upgrade`. When that's impossible (data deleted without backup,
lossy conversion), set `reversible = False` (you may then omit `downgrade`). `downgrade`
refuses to pass such a revision unless `--force`, which runs whatever downgrade code exists
and un-tracks the rest; data is not restored.

Prefer `backup=True` for deletions: the migration stays reversible and
`mongomig backups --drop <revision>` cleans up once you're confident.
