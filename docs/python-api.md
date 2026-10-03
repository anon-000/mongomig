# Python API

Everything is importable from `mongomig`.

## Running migrations

```python
import mongomig

mongomig.upgrade()                          # to head
mongomig.upgrade("7be2", env="staging")     # to a revision, with an environment overlay
mongomig.upgrade(steps=1)
mongomig.downgrade()                        # one step
mongomig.downgrade("base", allow_irreversible=False)
```

Common parameters: `config` (path to `mongomig.yaml`, default: discovered from the cwd),
`env`, `lock_timeout` (seconds to wait for another runner, default 0), `reporter` (see below).
`upgrade(..., yes=True)` allows migrations that need confirmation (destructive/irreversible,
or everything with `execution.confirm: always`); without it they raise
`ConfirmationRequiredError`.

Each returns a `RunResult` with `.steps` (`revision`, `message`, `duration_ms`) and
`.revisions`.

### FastAPI / async

```python
from contextlib import asynccontextmanager
from mongomig import aupgrade_to_head

@asynccontextmanager
async def lifespan(app):
    await aupgrade_to_head()   # runs in a thread; waits up to 120s for another worker's lock
    yield
```

`upgrade_to_head()` is the sync version. Both accept `config`, `env`, `lock_timeout`,
`reporter`, `yes`.

### State and readiness

```python
state = mongomig.current_state()        # read-only
state.pending          # ["7be204a1c9e0", ...] revisions not applied yet
state.failed           # failed or interrupted revisions
state.applied_heads    # where the database is
```

In async code: `await asyncio.to_thread(mongomig.current_state)`. A readiness endpoint built on
it is in the [FastAPI recipe](recipes/fastapi.md).

### Testing a migration

```python
migration = mongomig.load_revision("split_customer_names")   # id, prefix or file-name part
migration.upgrade(MigrationContext(test_db))
```

See [Test your migrations](recipes/testing.md).

### Drift

```python
from mongomig import check_drift

for collection in check_drift():                 # all registered collections
    for finding in collection.failed:             # over the configured thresholds
        print(collection.name, finding.path, finding.summary)
```

`check_drift(collections=None, *, config=None, env=None, sample_size=None, full_scan=False)`
returns one `CollectionDrift` per collection (`.findings`, `.failed`, `.documents_scanned`,
`.complete`). Each `DriftFinding` has `kind`, `path`, `status` (`"fail"`/`"warn"`),
`summary`, `share` (0–1), `threshold` and `hint`.

### Errors

All errors derive from `mongomig.MongoMigError` and carry `.message`, `.suggestion`,
`.details` and `.exit_code`. Notable subclasses (in `mongomig.errors`):
`MigrationExecutionError`, `ConfirmationRequiredError`, `LockError`,
`ChecksumMismatchError`, `MultipleHeadsError`, `ConfigError`.

## Models

```python
from mongomig import MongoMetadata, collection, Index

metadata = MongoMetadata.default(storage="json", exclude_none=True)

@collection("users", indexes=[Index("email", unique=True)], validator="auto")
class User(BaseModel): ...

metadata.register(Order, "orders", indexes=[Index([("user_id", 1), ("at", -1)])])
metadata.register_beanie(Product)

schemas, warnings = metadata.schemas()      # normalised CollectionSchema objects
```

- `MongoMetadata(storage=..., by_alias=..., exclude_none=..., exclude_unset=...,
  type_overrides=...)`; `.default(**profile)` is the process-wide registry `@collection`
  fills; `.configure(**profile)` changes the profile.
- `collection(name, indexes=(), validator=None, validation_level="moderate",
  validation_action="error", metadata=None)`
- `Index(keys, name=None, unique=False, sparse=False, **options)`

## MigrationContext

```python
from pymongo import MongoClient
from mongomig import MigrationContext

ctx = MigrationContext(MongoClient()["app"], batch_size=500, revision="manual")
ctx.ops.create_index("users", "email", unique=True)
```

`ctx.transaction()` and `ctx.batches(...)` work here too (without checkpoints: a standalone
context doesn't resume). Useful for tests of migration logic and one-off scripts. See
[Writing migrations](writing-migrations.md) for `ctx.ops`.

## Progress reporting

Pass a `Reporter` subclass to receive events (`migration_started`, `migration_finished`,
`migration_failed`, `log`, `warn`, `progress`). The default for the Python API is
`LoggingReporter`, which logs to the `mongomig` logger.
