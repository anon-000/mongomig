# Concepts

## Three views of a schema

MongoDB doesn't enforce one schema per collection, so "the schema" is ambiguous. MongoMig
keeps three views apart and never treats an inferred schema as truth:

| View | Source | Used for |
|---|---|---|
| **Declared** | your models (via `migrations/env.py`) | what the code expects now |
| **Snapshot** | `migrations/schema_snapshot.json` | what the code expected at the last migration |
| **Observed** | sampled documents (`mongomig inspect`) | what is actually stored |

`diff` and `revision --autogenerate` compare **declared vs snapshot**. This is deterministic
(same result on every machine, offline, in CI) and messy legacy data never changes what gets
generated. The live database is consulted only by `inspect`, `plan` and `--dry-run`.

## The snapshot

`schema_snapshot.json` is written by `revision --autogenerate` and `baseline`, and committed
with the revision. Each revision records the snapshot's hash (`snapshot_hash`); if the file
changes afterwards (hand edit, bad merge) `diff` warns. Two branches that both change models
both change the snapshot, so the conflict shows up in git review, and `mongomig heads` shows
two heads until you `mongomig merge`.

## Registering models

`MongoMetadata` is the registry MongoMig reads (like Alembic's `MetaData`). Fill it with:

- `@collection("name", indexes=[...], validator=None | "auto" | {...})` on Pydantic models;
- `metadata.register(Model, "name", ...)` in `env.py`;
- `metadata.register_beanie(*Documents)` for Beanie.

`validator=None` (default) means MongoMig doesn't manage the collection's validator.
`"auto"` generates a `$jsonSchema` from the model (`validationLevel: moderate` by default:
existing non-conforming documents don't block writes).

## Storage profiles

The stored BSON type depends on how documents are written, not just on the model:

| Profile | Written with | `datetime` | `UUID` | `Decimal` | `ObjectId` | `date` |
|---|---|---|---|---|---|---|
| `python` | `model_dump()` + PyMongo | date | binData¹ | decimal¹ | objectId | ⚠ not storable |
| `json` | `model_dump(mode="json")`, `jsonable_encoder` | string | string | string | string | string |
| `beanie` | Beanie | date | binData | decimal | objectId | date |

¹ needs `uuidRepresentation="standard"` / a `Decimal128` codec with plain PyMongo. MongoMig
warns about such cases (`mongomig models`, `mongomig doctor`).

Other options: `by_alias` (store alias names, default true), `exclude_none`, `exclude_unset`
(match your `model_dump` flags; they decide which fields are always present), and
`type_overrides={MyType: "string"}`.

**"Required"** in MongoMig means *present in every stored document*: with `model_dump()` every
model field is written, so every field is required unless `exclude_none`/`exclude_unset`
says otherwise. `int` and `long` are one family (PyMongo picks by value).

## Classifications

Every change `diff` finds is classified:

| | Meaning | Autogenerate writes |
|---|---|---|
| `SAFE` | compatible with existing data | nothing, or harmless DDL (index) |
| `WARNING` | operational risk or data left behind | the change (index/validator) or a commented suggestion |
| `REQUIRES_DATA_MIGRATION` | existing documents must change | the data change (backfill/rename) |
| `MANUAL_REVIEW` | needs a human decision | a commented `TODO(review)` block |
| `DESTRUCTIVE` | would lose data | never live code |

See [Autogenerate](autogenerate.md) for the full table.

## Revisions and the graph

A revision file has a random 12-hex `revision` id and a `down_revision` (its parent, or a
tuple of parents for a merge). Order comes from these links, not file names. Several heads
mean diverged history: `revision` and `upgrade` refuse until you pick `--head` or
`mongomig merge`. Ids can be abbreviated to a unique prefix of 4+ characters.

## Tracking

`__mongomig_migrations` holds one document per revision: status (`applied`, `failed`,
`running` = interrupted), checksum of the file, timing, and who ran it where (host, user,
environment, git commit). `__mongomig_lock` holds the migration lock. Backups live in
`__mongomig_backup_*` collections.
