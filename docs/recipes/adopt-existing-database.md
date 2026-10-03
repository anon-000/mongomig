# Adopt MongoMig on an existing database

**Situation:** your app has been running for a while. The database already has collections,
indexes and data, and you never used migrations. You want MongoMig to track changes **from
now on** without touching what's there.

## 1. Initialise and describe your models

```console
$ mongomig init
Initialised MongoMig in /srv/myapp
  + mongomig.yaml
  + migrations/env.py
  + migrations/schema_snapshot.json
  + migrations/versions/

Next steps:
  1. export MONGODB_URI=mongodb://localhost:27017
  2. mongomig revision -m "initial"
  3. mongomig current
```

Register your models in `migrations/env.py` (`import app.models`) and describe them as they
are today:

```python
from datetime import datetime

from pydantic import BaseModel
from mongomig import Index, collection


@collection("customers", indexes=[Index("email", unique=True, name="customers_email_unique")])
class Customer(BaseModel):
    name: str
    email: str
    phone: str | None = None
    created_at: datetime
```

```console
$ mongomig models
customers  app.models.Customer
  _id         objectId       
  name        string         
  email       string         
  phone       string | null  default=None
  created_at  date           
  indexes: customers_email_unique (email ↑, unique)

storage profile: python, by_alias
```

## 2. Baseline

```console
$ mongomig baseline
Created baseline revision 708558de8f9e → migrations/versions/20261003_1852_708558de8f9e_baseline.py
  snapshot: 1 collection(s): customers

Next: `mongomig upgrade` (marks the baseline applied; changes nothing).
```

The baseline revision is empty. It records "the models as they are now" in
`schema_snapshot.json`, so future `diff`s compare against today's state instead of treating
everything as new.

```console
$ mongomig upgrade
Running upgrade <base> -> 708558de8f9e, baseline
  ✓ done in 0ms
Applied 1 revision(s).
```

Run `mongomig upgrade` once in every environment (dev, staging, production). It changes
nothing in the database; it only records the baseline.

## 3. See how well the data matches the models

The models describe what the code expects. Years of data rarely match exactly:

```console
$ mongomig inspect customers
Collection: customers
Documents: 10,000 random sample of ~20,000  (0.06s)

Fields (nested presence is relative to the parent object):
  field       presence   types
  _id          100.00%   objectId
  name         100.00%   string
  email        100.00%   string
  created_at   100.00%   date 86.0% · string 14.1%
  phone         24.78%   string
  vip            1.86%   bool

Indexes:
  _id_ (_id ↑)
  customers_email_unique (email ↑, unique)

Validator: none

Based on 10,000 sampled documents; documents outside the sample may differ. Use --full-scan for a complete analysis.
```

```console
$ mongomig drift

CUSTOMERS  10,000 sampled of ~20,000 documents
  ✗ created_at  expected date · observed string 14.0%  > 0.5%
                hint: convert the values in a migration, or widen the model's type
  ✗ vip         not in the model · present in 2.02% of documents  > 1%
                hint: add it to the model, or remove it: ctx.ops.unset_field(..., backup=True)

2 failed, 0 below threshold
Based on samples: documents outside the sample may differ. Use --full-scan for an exact answer.
```

That's a to-do list, not an emergency:

- `created_at` stored as strings (14%) by an older app version → convert them with a
  migration, as in [Change a field's type](change-a-type.md);
- `vip` exists in 2% of documents but not in the model → add `vip: bool = False` to the
  model, or remove it as in [Remove a field](remove-a-field.md).

!!! tip "Pick the right storage profile"
    If *most* dates (or ObjectIds) are strings, your app probably writes documents with
    `jsonable_encoder` / `model_dump(mode="json")`. Set `storage="json"` in `env.py`
    before the baseline; `drift` hints at this when it sees it.
