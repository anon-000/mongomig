# Getting started

## Install

```bash
pip install mongomig            # plain Pydantic / PyMongo projects
pip install "mongomig[beanie]"  # Beanie projects
```

Requirements: Python 3.11+, MongoDB 6.0+ (tested on 6.0, 7.0, 8.0). No local MongoDB? The
repository's `docker-compose.yml` starts one: `docker compose up -d --wait`.

## 1. Initialise

```bash
cd your-project
mongomig init
```

```text
mongomig.yaml                   connection + execution settings (no secrets)
migrations/
├── env.py                      Python: tells MongoMig about your models
├── schema_snapshot.json        what the models looked like at the last migration
└── versions/                   one file per revision
```

`mongomig.yaml` reads the connection string from the environment:

```bash
export MONGODB_URI="mongodb://localhost:27017/?directConnection=true"
export MONGODB_DATABASE=myapp          # or set database.name / put it in the URI
mongomig doctor                        # checks config, connection, permissions
```

## 2. Register your models

Plain Pydantic (decorator in your models module):

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

import app.models  # noqa: F401  (importing registers @collection models)

target_metadata = MongoMetadata.default(storage="python")
```

Beanie: `target_metadata.register_beanie(User, Order)` (nothing to add to the Documents).

Pick the `storage` profile matching how your app writes documents (see
[Concepts](concepts.md#storage-profiles)); `model_dump()` → `"python"`, `jsonable_encoder` /
`model_dump(mode="json")` → `"json"`. Check the result:

```bash
mongomig models
```

## 3a. New project: generate the first migration

```bash
mongomig revision --autogenerate -m "initial schema"
mongomig upgrade
mongomig current
```

## 3b. Existing database: baseline

Your database already matches your models; you just want MongoMig to track changes from now
on:

```bash
mongomig baseline      # snapshot the current models; a no-op revision
mongomig upgrade       # marks the baseline as applied, changes nothing
mongomig inspect       # optional: see how real data compares to your models
```

## 4. The everyday loop

```bash
# edit app/models.py
mongomig diff                                   # what changed?
mongomig revision --autogenerate -m "add phone" # generate
# review the file, commit it together with migrations/schema_snapshot.json
mongomig plan                                   # impact against real data
mongomig upgrade
```

In CI, `mongomig validate` fails the build if a model change has no migration. See
[Production guide](production.md) for deploying.
