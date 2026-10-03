# Add a field with a default

**Situation:** you add `status: str = "active"` (and an optional `plan`) to a model. Existing
documents don't have them yet.

## 1. Change the model

```python
@collection("users", indexes=[Index("email")])
class User(BaseModel):
    name: str
    email: str
    status: str = "active"          # new
    plan: str | None = None          # new, optional
```

## 2. See what MongoMig detects

```console
$ mongomig diff
Schema changes (models vs migrations/schema_snapshot.json):

USERS
  + status: string = 'active'    REQUIRES_DATA_MIGRATION  backfill existing documents with 'active'
  + plan: string | null = None   SAFE  defaults to None: existing documents need no backfill

1 REQUIRES_DATA_MIGRATION · 1 SAFE

Generate a migration with: mongomig revision --autogenerate -m "describe it"
```

- `status` has a default, so existing documents get it written (**backfill**). Otherwise
  queries such as `{"status": "active"}` would miss every old document.
- `plan` defaults to `None`. Old documents already read as `None`, and `{"plan": null}`
  matches missing fields too, so there's nothing to do.

## 3. Generate the migration

```console
$ mongomig revision --autogenerate -m "add status and plan"
Detected:

USERS
  + status: string = 'active'    REQUIRES_DATA_MIGRATION  backfill existing documents with 'active'
  + plan: string | null = None   SAFE  defaults to None: existing documents need no backfill

1 REQUIRES_DATA_MIGRATION · 1 SAFE
Generated e4f1c38b9d65 → migrations/versions/20261003_1851_e4f1c38b9d65_add_status_and_plan.py
  updated: migrations/schema_snapshot.json
Review the file, then run `mongomig upgrade`.
```

The generated file (only the functions are shown; the docstring lists the changes):

```python
def upgrade(ctx):
    # status: new required field; backfill existing documents
    ctx.ops.backfill("users", {"status": {"$exists": False}}, {"$set": {"status": "active"}})


def downgrade(ctx):
    ctx.ops.unset_field("users", "status")
```

## 4. Check the impact on real data, then apply

```console
$ mongomig plan
Migration plan for shop

  4e3c7332253a  initial schema                           applied
  e4f1c38b9d65  add status and plan                      pending

e4f1c38b9d65  add status and plan   Risk: LOW
  users  backfill  $set status  ~50,000 docs · collection scan
  reversible: yes · deletes data: no · resumable: yes (checkpointed)

1 migration(s) · ~50,000 document writes · highest risk LOW
Estimates only; nothing was changed.
```

```console
$ mongomig upgrade
Running upgrade 4e3c7332253a -> e4f1c38b9d65, add status and plan
  • backfill users: 50,000 modified (50,000 matched, 50 batches)
  ✓ done in 788ms
Applied 1 revision(s).
```

```console
$ mongomig current
Database: shop
Current:  e4f1c38b9d65  add status and plan
Pending:  none — up to date
```

!!! tip
    Commit the revision file **and** `migrations/schema_snapshot.json` together. CI runs
    `mongomig validate`, which fails if a model change has no migration.
