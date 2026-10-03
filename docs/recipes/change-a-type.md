# Change a field's type (string → int)

**Situation:** an old version of the app stored `age` as a string (`"27"`). The model should
say `int`. This is the change autogenerate **won't** do for you: converting can lose data, so
it writes a reviewable draft instead.

## 1. Look at the data first

```console
$ mongomig inspect users
Collection: users
Documents: 10,000 random sample of ~50,000  (0.10s)

Fields (nested presence is relative to the parent object):
  field         presence   types
  _id            100.00%   objectId
  email          100.00%   string
  legacy_flags   100.00%   array<int>
  age            100.00%   int 66.0% · null 30.0% · string 4.0%
  status         100.00%   string
  display_name   100.00%   string

Indexes:
  _id_ (_id ↑)
  email_1 (email ↑)

Validator: none

Based on 10,000 sampled documents; documents outside the sample may differ. Use --full-scan for a complete analysis.
```

About 4% of `age` values are strings.

## 2. Change the model and ask drift how much data doesn't fit

```python
class User(BaseModel):
    age: int | None = None      # was: int | str | None
```

```console
$ mongomig drift users

USERS  10,000 sampled of ~50,000 documents
  ✗ age  expected int | null · observed string 4.15%  > 0.5%
         hint: convert the values in a migration, or widen the model's type

1 failed, 0 below threshold
Based on samples: documents outside the sample may differ. Use --full-scan for an exact answer.
```

## 3. Generate: you get a `TODO(review)` draft

```console
$ mongomig diff
Schema changes (models vs migrations/schema_snapshot.json):

USERS
  ~ age: int | string | null → int | null   MANUAL_REVIEW  existing values must be converted (possible data loss): review the generated conversion

1 MANUAL_REVIEW

Generate a migration with: mongomig revision --autogenerate -m "describe it"
```

```console
$ mongomig revision --autogenerate -m "age is an int"
Detected:

USERS
  ~ age: int | string | null → int | null   MANUAL_REVIEW  existing values must be converted (possible data loss): review the generated conversion

1 MANUAL_REVIEW
Generated f91f7962b9d4 → migrations/versions/20261003_1851_f91f7962b9d4_age_is_an_int.py
  updated: migrations/schema_snapshot.json
1 item(s) need review (search the file for TODO(review)).
Review the file, then run `mongomig upgrade`.
```

```python
def upgrade(ctx):
    # TODO(review): age: existing values must be converted (possible data loss): review the generated conversion
    # Values that can't be converted to long become null; check them first.
    # ctx.ops.backfill(
    #     "users",
    #     {"age": {"$type": "string"}},
    #     [
    #         {
    #             "$set": {
    #                 "age": {
    #                     "$convert": {
    #                         "input": "$age",
    #                         "to": "long",
    #                         "onError": None,
    #                         "onNull": None,
    #                     },
    #                 },
    #             },
    #         },
    #     ],
    # )
    pass


def downgrade(ctx):
    pass
```

Nothing runs until you decide. The draft converts only the documents where `age` is a
string, using `$convert`, and turns values that can't be converted into `null`
(`onError: None`).

## 4. Review and edit

Check the values that won't convert *before* running:

```javascript
// mongosh: string ages that aren't plain integers
db.users.find({ age: { $type: "string", $not: /^\d+$/ } }, { age: 1 })
```

If the result is fine, uncomment the block, remove the `TODO(review)` line and the `pass`:

```python
def upgrade(ctx):
    # Values that can't be converted to long become null; check them first.
    ctx.ops.backfill(
        "users",
        {"age": {"$type": "string"}},
        [
            {
                "$set": {
                    "age": {
                        "$convert": {
                            "input": "$age",
                            "to": "long",
                            "onError": None,
                            "onNull": None,
                        },
                    },
                },
            },
        ],
    )


def downgrade(ctx):
    pass
```

!!! tip "Other choices"
    Use `"onError": "$age"` to leave unconvertible values as they are (drift will keep
    reporting them), or fix them by hand first. If converting isn't worth it, keep
    `age: int | str | None` in the model.

## 5. Check, apply, verify

```console
$ mongomig plan
Migration plan for shop

  4e3c7332253a  initial schema                           applied
  e4f1c38b9d65  add status and plan                      applied
  19f6d1e042c8  rename name                              applied
  f91f7962b9d4  age is an int                            pending

f91f7962b9d4  age is an int   Risk: LOW
  users  backfill  pipeline update  ~2,052 docs · collection scan
  reversible: yes · deletes data: no · resumable: yes (checkpointed)

1 migration(s) · ~2,052 document writes · highest risk LOW
Estimates only; nothing was changed.
```

```console
$ mongomig upgrade
Running upgrade 19f6d1e042c8 -> f91f7962b9d4, age is an int
  • backfill users: 2,052 modified (2,052 matched, 3 batches)
  ✓ done in 104ms
Applied 1 revision(s).
```

```console
$ mongomig drift users

USERS  10,000 sampled of ~50,000 documents
  ✓ matches the model

0 failed, 0 below threshold
Based on samples: documents outside the sample may differ. Use --full-scan for an exact answer.
```
