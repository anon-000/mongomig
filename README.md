# MongoMig

[![PyPI](https://img.shields.io/pypi/v/mongomig)](https://pypi.org/project/mongomig/)
[![Python](https://img.shields.io/pypi/pyversions/mongomig)](https://pypi.org/project/mongomig/)
[![CI](https://github.com/anon-000/mongomig/actions/workflows/ci.yml/badge.svg)](https://github.com/anon-000/mongomig/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](https://github.com/anon-000/mongomig/blob/main/LICENSE)

**Alembic-style schema evolution and migrations for MongoDB.**

Change your models, see exactly what changed, generate a migration, review its impact, and
run it safely. Built for Python services (FastAPI, Flask, workers) on PyMongo or Beanie.

```console
$ mongomig diff
USERS
  + status: string = 'active'                    REQUIRES_DATA_MIGRATION  backfill existing documents with 'active'
  + age: int | null = None                       SAFE  defaults to None: existing documents need no backfill
  + given_name: string                           MANUAL_REVIEW  required, with no default: choose a value for existing documents
  - first_name                                   WARNING  removed from the model; existing data is kept (not deleted)
  + index users_email_unique (email ↑, unique)   WARNING  fails if existing documents contain duplicates

Possible rename: users.first_name → given_name (85% similar). If so, pass --rename users.first_name:given_name

$ mongomig revision --autogenerate -m "evolve users" --rename users.first_name:given_name
Generated b91baf4fd592 → migrations/versions/20260925_1115_b91baf4fd592_evolve_users.py

$ mongomig plan
b91baf4fd592  evolve users   Risk: HIGH
  users  rename_field  $rename first_name           ~4,218,901 docs · collection scan
  users  backfill      $set status                  ~4,218,901 docs · collection scan
  users  create_index  users_email_unique (unique)  4,218,901 docs
         ⚠ will fail: duplicate values exist, e.g. {'email': 'sam@example.com'}

$ mongomig upgrade
```

## Why MongoMig

- **Autogenerate from your models**: plain Pydantic (`@collection`) or Beanie documents, no
  extra declarations. Backfills with your defaults, renames, indexes, `$jsonSchema` validators.
- **Every change is classified**: SAFE, WARNING, REQUIRES_DATA_MIGRATION or MANUAL_REVIEW.
  Risky changes become commented `TODO(review)` blocks, and data is never deleted
  automatically.
- **Deterministic diffs**: models are compared with a committed schema snapshot rather than a
  sampled database, so results are the same on every machine and in CI.
- **Knows how you store data**: `model_dump()` vs `jsonable_encoder` stores dates as different
  BSON types, and MongoMig warns about types PyMongo can't store.
- **Production-grade execution**: distributed lock, idempotent batched operations with
  progress and ETA, retries, failure tracking, checksums, confirmation before destructive
  migrations, and restorable backups.
- **See the impact first**: `plan` / `--dry-run` estimate documents touched and collection
  scans, and predict unique-index failures and validator rejections against real data.
- **Built for CI**: `mongomig validate`, `--json` output everywhere, documented exit codes.

## Install

```bash
pip install mongomig            # add [beanie] for Beanie support
```

Python 3.11+, MongoDB 6.0+ (tested on 6.0, 7.0, 8.0).

## Quick start

```bash
mongomig init                                   # mongomig.yaml + migrations/
export MONGODB_URI="mongodb://localhost:27017"
# register your models in migrations/env.py (see below), then:
mongomig revision --autogenerate -m "initial"   # new project
mongomig baseline                               # ...or an existing database
mongomig upgrade
```

```python
# app/models.py
from pydantic import BaseModel
from mongomig import collection, Index

@collection("users", indexes=[Index("email", unique=True)])
class User(BaseModel):
    name: str
    email: str
    status: str = "active"
```

```python
# migrations/env.py
from mongomig import MongoMetadata

import app.models  # noqa: F401

target_metadata = MongoMetadata.default(storage="python")   # or "json"; Beanie: register_beanie(...)
```

**Everyday loop:** edit models → `mongomig diff` → `mongomig revision --autogenerate -m "..."` →
review and commit → CI `mongomig validate` → deploy `mongomig plan` + `mongomig upgrade`.

## Documentation

- [Getting started](https://github.com/anon-000/mongomig/blob/main/docs/getting-started.md)
- [Concepts](https://github.com/anon-000/mongomig/blob/main/docs/concepts.md): snapshots, storage profiles, classifications
- [Writing migrations](https://github.com/anon-000/mongomig/blob/main/docs/writing-migrations.md): `ctx` and `ctx.ops` reference
- [Autogenerate](https://github.com/anon-000/mongomig/blob/main/docs/autogenerate.md): what gets generated and how to review it
- [Production guide](https://github.com/anon-000/mongomig/blob/main/docs/production.md): permissions, plan, locking, backups, recovery
- [Coming from Alembic or hand-written scripts?](https://github.com/anon-000/mongomig/blob/main/docs/comparison.md)
- [CLI reference](https://github.com/anon-000/mongomig/blob/main/docs/cli.md) · [Python API](https://github.com/anon-000/mongomig/blob/main/docs/python-api.md) ·
  [Troubleshooting](https://github.com/anon-000/mongomig/blob/main/docs/troubleshooting.md)

Examples: [FastAPI + Pydantic](https://github.com/anon-000/mongomig/tree/main/examples/fastapi_pydantic) ·
[FastAPI + Beanie](https://github.com/anon-000/mongomig/tree/main/examples/fastapi_beanie)

## Status

The MVP is complete (config, revisions, upgrade/downgrade, schema inspection, diff,
autogenerate, plan/dry-run, locking, batching, backups, CI checks). Planned next: drift
detection (models vs live data), resumable checkpoints, transaction helpers, migration
squashing.

## Development

```bash
make install        # uv venv + editable install with dev extras
make mongo-up       # MongoDB 7 single-node replica set in Docker
make check          # ruff + mypy --strict + pytest
```

Integration tests skip when MongoDB isn't running. To test another MongoDB version, run
`MONGO_VERSION=8.0 make mongo-up`.

### Releasing

1. Bump `src/mongomig/_version.py` and update `CHANGELOG.md`, then merge to `main`.
2. Create a GitHub Release with tag `v<version>` and publish it. The release workflow checks the
   version, builds and smoke-tests the wheel, and publishes to PyPI via Trusted Publishing.

## Contributing

Bug reports, ideas and pull requests are welcome. See [CONTRIBUTING.md](https://github.com/anon-000/mongomig/blob/main/CONTRIBUTING.md).

## License

MIT
