# MongoMig

**Alembic-style schema evolution and migrations for MongoDB.**

Built for Python services (FastAPI, Flask, workers) on PyMongo, Motor or Beanie.

> **Status: alpha.** The core workflow works end to end: models → diff → autogenerate →
> review → upgrade/downgrade. Expect rough edges; feedback is welcome.

```console
$ mongomig diff
USERS
  + status: string = 'active'                    REQUIRES_DATA_MIGRATION  backfill existing documents with 'active'
  + age: int | null = None                       SAFE  defaults to None: existing documents need no backfill
  - first_name                                   WARNING  removed from the model; existing data is kept (not deleted)
  + given_name: string                           MANUAL_REVIEW  required, with no default: choose a value for existing documents
  + index users_email_unique (email ↑, unique)   WARNING  fails if existing documents contain duplicates

Possible rename: users.first_name → given_name (85% similar). If so, pass --rename users.first_name:given_name

$ mongomig revision --autogenerate -m "evolve user schema" --rename users.first_name:given_name
Generated b91baf4fd592 → migrations/versions/20260925_1115_b91baf4fd592_evolve_user_schema.py

$ mongomig upgrade
Running upgrade 13a4cc638050 -> b91baf4fd592, evolve user schema
  • rename_field users: 4,218,901 modified (4,218,901 matched, 4219 batches)
  • backfill users: 4,218,901 modified (4,218,901 matched, 4219 batches)
  • index users.users_email_unique ready
  ✓ done in 8:42
Applied 1 revision(s).
```

## Install

```bash
pip install --pre mongomig          # add [beanie] for Beanie support
```

Requires Python 3.11+ and MongoDB 6.0+.

## Quick start

```bash
mongomig init                                   # creates mongomig.yaml + migrations/
# register your models in migrations/env.py (see "Registering your models")
export MONGODB_URI="mongodb://localhost:27017"

mongomig revision --autogenerate -m "initial"   # new project: generate from your models
# ...or, for an existing database:
mongomig baseline                               # snapshot current models, no data changes

mongomig upgrade                                # apply pending revisions
mongomig current                                # what's applied vs pending
```

### The workflow

```text
1. Change your models
2. mongomig diff                                   see what changed (offline, deterministic)
3. mongomig revision --autogenerate -m "..."       generate the migration + update the snapshot
4. Review the file (search for TODO(review)), commit both files
5. CI: mongomig diff --check                       fails if a model change has no migration
6. Deploy: mongomig plan                           impact + risk against the real data
           mongomig upgrade
```

`diff` compares your models with `migrations/schema_snapshot.json`, the committed record of what
the models looked like at the last migration. It does **not** compare against the live
database, so results are the same on every machine and in CI, and messy legacy data never
changes what gets generated. Use `mongomig inspect` to look at the real data.

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
| `create_collection(name, validator=None, …)` / `drop_collection(name, backup=False)` / `rename_collection` | create is skipped if the collection exists |
| `set_validator(coll, validator, level="moderate")` / `remove_validator(coll)` | `$jsonSchema` validators |
| `backfill(coll, filter, update, batch_size=None)` | `update` may be an aggregation pipeline |
| `unset_field(coll, field, filter=None, backup=False)` / `rename_field(coll, old, new)` | batched; `backup=True` keeps restorable copies |
| `restore_field(coll, field)` / `restore_collection(name)` | undo `backup=True` removals |

For anything else, `ctx.collection("users")` is a plain PyMongo collection, and
`ctx.unsafe_db` is the raw database.

Declare `reversible = False` (and optionally omit `downgrade`) when a migration can't be
undone. `downgrade` then refuses to pass through it unless you add `--force`.

### What autogenerate writes

Every change is classified, and only changes that autogenerate can do *correctly* become live
code:

| Change | Classification | Generated |
|---|---|---|
| New optional field, or default `None` | SAFE | nothing (old documents read fine) |
| New required field with a default | REQUIRES_DATA_MIGRATION | `backfill` with the default (downgrade: `unset_field`) |
| Renamed field (`--rename coll.old:new`) | REQUIRES_DATA_MIGRATION | `rename_field` both ways |
| New/removed/changed index | SAFE / WARNING | `create_index` / `drop_index` both ways |
| Validator added/changed (`validator="auto"`) | WARNING | `set_validator` / `remove_validator` |
| New required field, no default (or computed default) | MANUAL_REVIEW | commented `TODO(review)` backfill |
| Unsafe type change (e.g. string → int) | MANUAL_REVIEW | commented `TODO(review)` `$convert` backfill |
| Field or collection removed from models | WARNING | commented `unset_field` / `drop_collection`: **data is never deleted automatically** |

Renames are never guessed. MongoMig suggests them ("Possible rename: first_name → given_name")
and you confirm with `--rename`.

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
| `diff [--check] [--rename C.OLD:NEW]` | no | Model changes since the last migration; `--check` exits 1 if any |
| `revision -m MSG [--autogenerate] [--rename C.OLD:NEW] [--head REV]` | no | Create a revision: empty, or generated from model changes |
| `baseline [-m MSG]` | no | Adopt MongoMig on an existing database: snapshot the models, no data changes |
| `heads` | no | Show head revision(s) |
| `history` | no | List revisions, newest first |
| `merge [REVS...] [-m MSG]` | no | Join several heads into one |
| `current [--check]` | yes | Applied vs pending (read-only); `--check` exits 1 if not up to date |
| `plan [TARGET]` | yes | Pending migrations with estimated impact and risk; changes nothing |
| `upgrade [TARGET] [--steps N] [--dry-run] [--yes]` | yes | Apply pending revisions. `TARGET`: `head` (default), `heads`, or a revision |
| `downgrade [TARGET] [--steps N] [--dry-run] [--yes] [--force]` | yes | Revert one step (default), back to `TARGET`, or `base` |
| `backups [--drop REV --yes]` | yes | List/drop backups made with `backup=True` |
| `stamp REV...` | yes | Mark revisions as applied **without running them** (baselines, checksum repair) |
| `models` | no | The schema your registered models declare, plus storage warnings |
| `inspect [COLL...] [--sample-size N \| --sample-percent P \| --full-scan]` | yes | The schema actually stored: fields, types, presence, indexes, validator |

Global options: `--config PATH`, `--env NAME`, `--json`, `--verbose`, `--version`.

Exit codes: `0` success · `1` validation · `2` execution/connection · `3` configuration ·
`4` revision conflict · `5` lock · `6` checksum mismatch.

### Before you run: `plan` and `--dry-run`

```console
$ mongomig plan
Migration plan for app (production)

  786c0408ba16  add status and unique email              pending

786c0408ba16  add status and unique email   Risk: HIGH
  users  backfill      $set status                  ~1,100,000 docs · collection scan
  users  create_index  users_email_unique (unique)  1,100,000 docs
         ⚠ will fail: duplicate values exist, e.g. {'email': 'u0@x.io'}
  reversible: yes · deletes data: no · resumable: yes (ctx.ops are idempotent)
  why HIGH: ~1.1M documents with a collection scan (users.backfill); expected to fail (users.create_index)

1 migration(s) · ~1,100,000 document writes · highest risk HIGH
Estimates only; nothing was changed.
```

`mongomig plan`, `upgrade --dry-run` and `downgrade --dry-run` run your migrations in
recording mode. Reads go to MongoDB, and writes are recorded with document estimates instead
of being executed. The report shows:
- whether a filter needs a collection scan
- whether a new unique index would fail on existing duplicates
- how many existing documents a new validator rejects
- whether anything deletes data

A migration using `ctx.unsafe_db` is reported as "not fully simulated". Migration code really
runs during a dry run (only its database writes are intercepted), so keep side effects inside
`ctx`.

### Safety

- **Confirmation**: `upgrade` asks before running migrations that can delete data or are
  irreversible, and in scripts it requires `--yes`. The check reads the migration code rather
  than running it. Set `execution.confirm: always` (e.g. in `mongomig.production.yaml`) to
  confirm every run, or `never` to turn it off. From Python, `upgrade_to_head(yes=True)`.
- **Backups**: `ctx.ops.unset_field(..., backup=True)` copies the values to
  `__mongomig_backup_<revision>` first, and `ctx.ops.drop_collection(..., backup=True)`
  renames the collection instead of dropping it. `restore_field` / `restore_collection` undo
  them (put them in `downgrade`). List or clean up with `mongomig backups`.
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
- [x] **M4 Autogenerate**: `diff`, `revision --autogenerate`, index/validator diff
- [x] **M5 Production safety**: `--dry-run`, `plan`, impact analysis, backups for destructive ops
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
