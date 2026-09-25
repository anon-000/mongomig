# MongoMig

**Alembic-style schema evolution and migrations for MongoDB.**

Built for Python services (FastAPI, Flask, workers) on PyMongo, Motor or Beanie.

> **Status: pre-alpha.** Writing and running migrations works (M1 + M2), and MongoMig can
> read your models and inspect your data (M3). Schema diff and autogenerate are next.

```console
$ mongomig revision -m "add user status"
Created revision d03be90b1aee → migrations/versions/20260925_1012_d03be90b1aee_add_user_status.py

$ mongomig upgrade
Running upgrade <base> -> d03be90b1aee, add user status
  • index users.users_email_unique ready
  backfill users: 154,000 / ~300,000 (51.3%) · 76,856 docs/s · elapsed 2.0s · ETA 1.9s
  backfill users: 300,000 / ~300,000 (100.0%) · 69,424 docs/s · elapsed 4.3s
  • backfill users: 300,000 modified (300,000 matched, 300 batches)
  ✓ done in 4.8s
Applied 1 revision(s).

$ mongomig current
Database: app
Current:  d03be90b1aee  add user status
Pending:  none — up to date
```

## Install

```bash
pip install --pre mongomig
```

Requires Python 3.11+ and MongoDB 6.0+.

## Quick start

```bash
mongomig init                                   # creates mongomig.yaml + migrations/
export MONGODB_URI="mongodb://localhost:27017"
mongomig revision -m "initial"                  # new revision file — edit it
mongomig upgrade                                # apply pending revisions
mongomig current                                # what's applied vs pending
```

`mongomig init` creates:

```text
mongomig.yaml                   # connection + execution settings (no secrets!)
migrations/
├── env.py                      # Python: points MongoMig at your models
├── schema_snapshot.json        # last known expected schema (used by autogenerate)
└── versions/                   # one file per revision
```

### Configuration

```yaml
database:
  uri: ${MONGODB_URI}                 # ${VAR} and ${VAR:-default} are expanded
  name: ${MONGODB_DATABASE:-app}
```

Per-environment overrides live in `mongomig.<env>.yaml` and are merged on top:

```bash
mongomig --env production current     # or MONGOMIG_ENV=production
```

MongoMig never prints passwords or full connection strings. It warns you if a password is
written directly into the config file.

### Writing migrations

```python
"""add user status"""

revision = "d03be90b1aee"
down_revision = "a1f3c9d20b44"
reversible = True


def upgrade(ctx):
    ctx.ops.create_index("users", "email", unique=True, name="users_email_unique")
    ctx.ops.backfill("users", {"status": {"$exists": False}}, {"$set": {"status": "active"}})


def downgrade(ctx):
    ctx.ops.unset_field("users", "status")
    ctx.ops.drop_index("users", "users_email_unique")
```

`ctx.ops` operations are **idempotent** (re-running a half-finished migration is safe) and
data changes are **batched** in `_id` order, with retries on transient errors and progress
output:

| Operation | Notes |
|---|---|
| `create_index(coll, keys, name=None, **opts)` | `keys`: `"email"`, `["a", "b"]`, `[("a", 1), ("b", -1)]`; opts: `unique`, `sparse`, `partialFilterExpression`, `expireAfterSeconds`, `collation`, `hidden`… |
| `drop_index(coll, name)` | skipped if missing |
| `create_collection(name, validator=None, …)` / `drop_collection` / `rename_collection` | create is skipped if the collection exists |
| `set_validator(coll, validator, level="moderate")` / `remove_validator(coll)` | `$jsonSchema` validators |
| `backfill(coll, filter, update, batch_size=None)` | `update` may be an aggregation pipeline |
| `unset_field(coll, field, filter=None)` / `rename_field(coll, old, new)` | batched |

For anything else, `ctx.collection("users")` is a plain PyMongo collection, and
`ctx.unsafe_db` is the raw database.

Declare `reversible = False` (and optionally omit `downgrade`) when a migration can't be
undone. `downgrade` then refuses to pass through it unless you add `--force`.

### Registering your models

MongoMig learns what your collections *should* look like from your models, in the same way
Alembic learns from SQLAlchemy's `MetaData`. You can register plain Pydantic models with a
decorator:

```python
# app/models.py
from pydantic import BaseModel
from mongomig import collection, Index


@collection("users", indexes=[Index("email", unique=True)], validator="auto")
class User(BaseModel):
    name: str
    email: str
    age: int | None = None
```

Or register them explicitly in `migrations/env.py`, which keeps MongoMig out of your model
modules:

```python
from mongomig import MongoMetadata, Index
from app.models import User

target_metadata = MongoMetadata.default(storage="python")
target_metadata.register(User, "users", indexes=[Index("email", unique=True)])
```

**Beanie** documents need no extra declarations, because the collection name, `Indexed(...)`
fields and `Settings.indexes` are read from the class:

```python
target_metadata.register_beanie(User, Order)
```

`validator="auto"` makes MongoMig manage a `$jsonSchema` validator generated from the model
(with `validationLevel: moderate` by default). `None` (the default) leaves validators alone.

#### Storage profile

The BSON type that ends up in MongoDB depends on how your app writes documents:

| `storage=` | Your code | `datetime` | `UUID` | `Decimal` | `ObjectId` |
|---|---|---|---|---|---|
| `"python"` | `coll.insert_one(m.model_dump())` | date | binData | decimal | objectId |
| `"json"` | `m.model_dump(mode="json")` / FastAPI `jsonable_encoder` | **string** | string | string | string |
| `"beanie"` | Beanie (automatic for `register_beanie`) | date | binData | decimal | objectId |

Also available: `by_alias`, `exclude_none`, `exclude_unset` (match your `model_dump` options)
and `type_overrides={MyType: "string"}`.

`mongomig models` shows the resulting schema. It also **warns about types PyMongo cannot store**
with your profile, such as `date`, `Decimal`, `Enum` members without `use_enum_values`, or
`UUID` without a `uuidRepresentation`:

```console
$ mongomig models
users  app.models.User
  _id         objectId
  name        string
  email       string
  age         int | null     default=None
  indexes: email_1 (email ↑, unique)
```

### Inspecting your data

```console
$ mongomig inspect users
Collection: users
Documents: 10,000 random sample of ~4,982,133  (0.41s)

Fields (nested presence is relative to the parent object):
  field         presence   types
  _id            100.00%   objectId
  name           100.00%   string
  age             63.20%   int 97.2% · string 2.8%
  profile         41.10%   object
    verified     100.00%   bool

Indexes:
  _id_ (_id ↑)
  users_email_unique (email ↑, unique)

Validator: none
```

Sampling options are `--sample-size N` (the default comes from config), `--sample-percent P`
and `--full-scan`. Sampled results always say so, because documents outside the sample may
differ.

### Revisions, branches, merges

Revision order comes from `down_revision`, not from the file name. Revision ids are random,
so two developers working on separate branches never get the same id. If both branches add a
revision, `mongomig heads` shows two heads, and `mongomig revision` refuses to continue until
you choose a parent with `--head`. `mongomig merge` joins the heads again.

Revision ids can be shortened to a unique prefix of 4 or more characters, the same way git
handles commit hashes.

### Commands

| Command | Needs MongoDB | Description |
|---|---|---|
| `init` | no | Create config and migrations directory |
| `revision -m MSG [--head REV]` | no | Create a new revision |
| `heads` | no | Show head revision(s) |
| `history` | no | List revisions, newest first |
| `merge [REVS...] [-m MSG]` | no | Join several heads into one |
| `current [--check]` | yes | Applied vs pending (read-only); `--check` exits 1 if not up to date |
| `upgrade [TARGET] [--steps N]` | yes | Apply pending revisions. `TARGET`: `head` (default), `heads`, or a revision |
| `downgrade [TARGET] [--steps N] [--yes] [--force]` | yes | Revert one step (default), back to `TARGET`, or `base` |
| `stamp REV...` | yes | Mark revisions as applied **without running them** (baselines, checksum repair) |
| `models` | no | The schema your registered models declare, plus storage warnings |
| `inspect [COLL...] [--sample-size N \| --sample-percent P \| --full-scan]` | yes | The schema actually stored: fields, types, presence, indexes, validator |

Global options: `--config PATH`, `--env NAME`, `--json`, `--verbose`, `--version`.

Exit codes: `0` success · `1` validation · `2` execution/connection · `3` configuration ·
`4` revision conflict · `5` lock · `6` checksum mismatch.

### Safety

- **Locking**: `upgrade`, `downgrade` and `stamp` take a distributed lock
  (`__mongomig_lock`) with a heartbeat, so two runners never migrate at the same time. Use
  `--lock-timeout SECONDS` to wait for another run instead of failing.
- **Checksums**: each applied revision stores a checksum of its file. If an already-applied
  file is edited, `upgrade` stops with exit code 6.
- **Failures** are recorded (`mongomig current` shows them) and the next `upgrade` retries the
  failed revision. Errors name the revision, operation, collection and how many documents were
  already processed.
- **Tracking** records who ran each migration, where, and at which git commit.

### FastAPI

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from mongomig import aupgrade_to_head


@asynccontextmanager
async def lifespan(app: FastAPI):
    await aupgrade_to_head()  # every worker may call this; one migrates, the others wait
    yield


app = FastAPI(lifespan=lifespan)
```

For production, run `mongomig upgrade` as a separate deploy step (CI job, Kubernetes Job)
before rolling out the new app version. The lifespan hook suits development and small
deployments. `mongomig current --check` works well as a readiness guard. The same API is
available synchronously: `mongomig.upgrade()`, `mongomig.downgrade()`, `mongomig.upgrade_to_head()`.

## Roadmap

- [x] **M1 Foundation**: config, CLI, revision files, revision graph, tracking
- [x] **M2 Migration engine**: `upgrade`, `downgrade`, locking, `merge`, checksums, FastAPI lifespan helper
- [x] **M3 Schema engine**: `@collection` models, Beanie support, `inspect`, snapshots
- [ ] **M4 Autogenerate**: `diff`, `revision --autogenerate`, index/validator diff
- [ ] **M5 Production safety**: `--dry-run`, `plan`, impact analysis, backups for destructive ops
- [ ] **M6 Release**: `validate`, `doctor`, docs, PyPI

## Development

```bash
make install        # uv venv + editable install with dev extras
make mongo-up       # MongoDB 7 single-node replica set in Docker
make check          # ruff + mypy --strict + pytest
```

Integration tests are skipped automatically when MongoDB isn't running. To test against another
MongoDB version, run `MONGO_VERSION=8.0 make mongo-up`.

### Releasing

1. Bump `src/mongomig/_version.py` and update `CHANGELOG.md`, then merge to `main`.
2. On GitHub, create a Release with tag `v<version>` (e.g. `v0.1.0`) and publish it.
3. `.github/workflows/release.yml` checks that the tag matches the version, builds and
   smoke-tests the wheel, then publishes to PyPI through Trusted Publishing.

## License

MIT
