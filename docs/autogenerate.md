# Autogenerate

```bash
mongomig diff                                          # review
mongomig revision --autogenerate -m "evolve users"     # generate + update the snapshot
```

`diff` compares your registered models with `migrations/schema_snapshot.json`; it needs no
database. `revision --autogenerate` writes a revision file and then rewrites the snapshot, so
the next diff starts from the new state. Commit both files together. If nothing changed, no
file is written.

## What gets generated

| Change | Class | Generated code |
|---|---|---|
| collection added | SAFE | `create_collection` (+ its indexes, validator) |
| collection removed from models | WARNING | commented `drop_collection(..., backup=True)`; data kept |
| optional field added (`exclude_none`/`exclude_unset`), or default `None` | SAFE | nothing: old documents read fine and `{f: null}` also matches missing fields |
| required field with a static default (`status: str = "active"`) | REQUIRES_DATA_MIGRATION | `backfill` missing documents with the default; downgrade `unset_field` |
| required nullable field without default (`x: int \| None`) | REQUIRES_DATA_MIGRATION | backfill `None` |
| required field without default, or with `default_factory` | MANUAL_REVIEW | commented `TODO(review)` backfill with `...` placeholder |
| field removed from model | WARNING | commented `unset_field(..., backup=True)`; data kept |
| field renamed (`--rename`) | REQUIRES_DATA_MIGRATION | `rename_field` both ways |
| type widened (`int` → `int \| str`) | SAFE | nothing |
| safe conversion (`int` → `float`) | WARNING | commented optional conversion |
| unsafe conversion (`str` → `int`) | MANUAL_REVIEW | commented `$convert` backfill (unconvertible → null) |
| nullable → not nullable | REQUIRES_DATA_MIGRATION / MANUAL_REVIEW | replace nulls with the default, or TODO |
| optional → required | like "required field added" | backfill where missing |
| enum values removed | MANUAL_REVIEW | commented update of documents holding removed values |
| index added | SAFE (WARNING if unique or TTL) | `create_index`; downgrade `drop_index` |
| index removed / changed | WARNING | `drop_index` / drop + create, reversed in downgrade |
| validator added / changed (managed) | WARNING | `set_validator`; downgrade restores the previous one |
| validator no longer managed | WARNING | commented `remove_validator` |

Nested object fields are diffed and backfilled with dotted paths (only where the parent object
exists). Changes to fields **inside arrays** (`items[].qty`) are always MANUAL_REVIEW. With a
**strict** managed validator, fields defaulting to `None` are backfilled too, since strict
validation rejects updates to documents missing a required field.

The upgrade runs in a safe order (collections → data → drop indexes → create indexes →
validators) and the downgrade in reverse.

## Renames

MongoMig never guesses a rename; a removed + added field pair is suggested instead:

```text
Possible rename: users.first_name → given_name (85% similar). If so, pass --rename users.first_name:given_name
```

```bash
mongomig revision --autogenerate -m "rename" --rename users.first_name:given_name
```

`--rename COLLECTION.OLD:NEW` (repeatable); for nested fields
`--rename users.profile.bio:about` renames `profile.bio` to `profile.about`.

## Reviewing a generated file

1. The docstring lists every change with its class, and a **Needs review** section.
2. Search for `TODO(review)`: these blocks are commented out on purpose. Fill in values or
   delete them.
3. Run `mongomig plan` against a realistic database to see document counts, collection scans
   and index-build risks.
4. Commit the revision and the snapshot together.

## CI

```bash
mongomig validate          # includes: every model change has a migration
mongomig diff --check      # just that check; exit 1 if models changed without a migration
```

## Adopting on an existing database

`mongomig baseline` writes the snapshot from the current models plus an empty revision.
Use it once, when your models already describe the data; afterwards use autogenerate.
`baseline --force` rebuilds the snapshot if it has diverged beyond repair.

## Limitations

- The snapshot describes the *models*; data that never matched them (legacy documents) is not
  fixed by autogenerate. Use `mongomig inspect` to find it and write a migration by hand.
- Collection renames are not detected; write `ctx.ops.rename_collection` yourself.
- Changing the storage profile makes many fields look changed (dates become strings, ...);
  `diff` warns when it happens.
