# Add a unique index when duplicates exist

**Situation:** emails should be unique. In production, a few accounts share one. Building
the index would fail halfway through a deploy. `plan` catches this before you deploy.

## 1. Declare it and generate

```python
@collection("users", indexes=[Index("email", unique=True, name="users_email_unique")])
class User(BaseModel):
    ...
```

```console
$ mongomig revision --autogenerate -m "unique email"
Detected:

USERS
  + index users_email_unique (email ↑, unique)   WARNING  fails if existing documents contain duplicates
  - index email_1 (email ↑)                      WARNING  queries relying on it may become slow

2 WARNING
Generated f8389ca45387 → migrations/versions/20261003_1852_f8389ca45387_unique_email.py
  updated: migrations/schema_snapshot.json
Review the file, then run `mongomig upgrade`.
```

```python
def upgrade(ctx):
    ctx.ops.drop_index("users", "email_1")

    ctx.ops.create_index("users", "email", name="users_email_unique", unique=True)


def downgrade(ctx):
    ctx.ops.drop_index("users", "users_email_unique")

    ctx.ops.create_index("users", "email", name="email_1")
```

## 2. Plan against production data

```console
$ mongomig plan
Migration plan for shop

  4e3c7332253a  initial schema                           applied
  e4f1c38b9d65  add status and plan                      applied
  19f6d1e042c8  rename name                              applied
  f91f7962b9d4  age is an int                            applied
  83eb731318d1  drop legacy_flags                        applied
  f8389ca45387  unique email                             pending

f8389ca45387  unique email   Risk: HIGH
  users  drop_index    email_1                      
  users  create_index  users_email_unique (unique)  50,000 docs
         ⚠ will fail: duplicate values exist, e.g. {'email': 'dup@example.com'}
  reversible: yes · deletes data: no · resumable: yes (checkpointed)
  why HIGH: expected to fail (users.create_index)

1 migration(s) · ~0 document writes · highest risk HIGH
Estimates only; nothing was changed.
```

## 3. Find and fix the duplicates

```python
# find them (pymongo)
dups = db.users.aggregate([
    {"$group": {"_id": "$email", "n": {"$sum": 1}, "ids": {"$push": "$_id"}}},
    {"$match": {"n": {"$gt": 1}}},
])
```

```text
dup@example.com: 3 documents
```

How to fix them is a business decision: merge the accounts, or change the extra emails. When
you've decided, plan again:

```console
$ mongomig plan
Migration plan for shop

  4e3c7332253a  initial schema                           applied
  e4f1c38b9d65  add status and plan                      applied
  19f6d1e042c8  rename name                              applied
  f91f7962b9d4  age is an int                            applied
  83eb731318d1  drop legacy_flags                        applied
  f8389ca45387  unique email                             pending

f8389ca45387  unique email   Risk: LOW
  users  drop_index    email_1                      
  users  create_index  users_email_unique (unique)  50,000 docs
  reversible: yes · deletes data: no · resumable: yes (checkpointed)

1 migration(s) · ~0 document writes · highest risk LOW
Estimates only; nothing was changed.
```

## 4. Apply

```console
$ mongomig upgrade
Running upgrade 83eb731318d1 -> f8389ca45387, unique email
  • dropped index users.email_1
  • index users.users_email_unique ready
  ✓ done in 164ms
Applied 1 revision(s).
```

!!! note
    The old `email_1` index is dropped first, because MongoDB doesn't allow two indexes on
    the same key pattern. While the new unique index builds, queries by email run without an
    index. On big collections, schedule this outside peak hours.
