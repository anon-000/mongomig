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

```python
def upgrade(ctx):
    users = ctx.collection("users")
    for user in users.find({"full_name": {"$exists": True}}, {"full_name": 1}):
        first, _, last = user["full_name"].partition(" ")
        users.update_one(
            {"_id": user["_id"]},
            {"$set": {"first_name": first, "last_name": last or None},
             "$unset": {"full_name": ""}},
        )
```

Guidelines:

- **Make it re-runnable.** A failed migration is retried from the start on the next
  `upgrade`. Filter on "not yet migrated" (here `full_name` exists) instead of "all documents".
- **Batch big loops** (or use `ctx.ops.backfill` with a pipeline update when the logic fits).
- **No side effects outside `ctx`.** `plan`/`--dry-run` run your function for real with writes
  intercepted; an HTTP call or email would happen.
- **Transactions:** not wrapped automatically. Use `ctx.unsafe_db.client.start_session()`
  yourself when you need one (replica set required); it won't be simulated by dry runs.

## Reversibility

Write a `downgrade` that undoes `upgrade`. When that's impossible (data deleted without backup,
lossy conversion), set `reversible = False` (you may then omit `downgrade`). `downgrade`
refuses to pass such a revision unless `--force`, which runs whatever downgrade code exists
and un-tracks the rest; data is not restored.

Prefer `backup=True` for deletions: the migration stays reversible and
`mongomig backups --drop <revision>` cleans up once you're confident.
