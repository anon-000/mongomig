# Rename a field

**Situation:** `name` becomes `display_name`. MongoMig never guesses renames; it suggests them,
and you confirm.

## 1. Change the model, look at the diff

```python
class User(BaseModel):
    display_name: str      # was: name
    ...
```

```console
$ mongomig diff
Schema changes (models vs migrations/schema_snapshot.json):

USERS
  + display_name: string   MANUAL_REVIEW  required, with no default: choose a value for existing documents
  - name                   WARNING  removed from the model; existing data is kept (not deleted)

Possible rename: users.name → display_name (75% similar). If so, pass --rename users.name:display_name
1 MANUAL_REVIEW · 1 WARNING

Generate a migration with: mongomig revision --autogenerate -m "describe it"
```

Without confirmation this would be "a new required field" plus "a removed field". The
**Possible rename** line gives you the exact flag to use instead.

## 2. Generate with `--rename`

```console
$ mongomig revision --autogenerate -m "rename name" --rename users.name:display_name
Detected:

USERS
  ~ name → display_name   REQUIRES_DATA_MIGRATION  rename in existing documents

1 REQUIRES_DATA_MIGRATION
Generated 19f6d1e042c8 → migrations/versions/20261003_1851_19f6d1e042c8_rename_name.py
  updated: migrations/schema_snapshot.json
Review the file, then run `mongomig upgrade`.
```

```python
def upgrade(ctx):
    ctx.ops.rename_field("users", "name", "display_name")


def downgrade(ctx):
    ctx.ops.rename_field("users", "display_name", "name")
```

`rename_field` works in batches and only touches documents that still have the old field,
so an interrupted run can simply be resumed. The downgrade renames it back.

## 3. Apply

```console
$ mongomig upgrade
Running upgrade e4f1c38b9d65 -> 19f6d1e042c8, rename name
  • rename_field users: 50,000 modified (50,000 matched, 50 batches)
  ✓ done in 747ms
Applied 1 revision(s).
```

!!! note "Nested fields and collections"
    Nested fields work the same way: `--rename users.profile.bio:about` renames
    `profile.bio` to `profile.about`. Collection renames aren't detected; write
    `ctx.ops.rename_collection("old", "new")` in a revision yourself
    (`mongomig revision -m "rename collection"`).
